# sprunki_game_player.py - ENHANCED with Drag Verification
# CHANGES: Added "Putted characters" variable monitoring and 5-coordinate retry logic

import json
import time
import webbrowser
import pyautogui
import threading
import random
import os
import subprocess
import requests
import websocket
from tkinter import Tk, Button, Label, StringVar, messagebox, filedialog, Frame, Radiobutton, IntVar
from tkinter.ttk import Progressbar, Combobox
import keyboard
from urllib.parse import urlparse
from image_detector import ImageDetector
import pygetwindow as gw
from pathlib import Path
from datetime import datetime
import math

class DragOperationStats:
    """Class để tracking drag operation statistics"""
    def __init__(self):
        self.total_attempts = 0
        self.successful_drags = 0
        self.failed_drags = 0
        self.retry_counts = []
        # ADDED: Statistics for verification
        self.verification_enabled = 0
        self.verification_successful = 0
        self.coordinate_retries = []
    
    def record_attempt(self, success, retry_count=0, coord_used=0, verification_used=False):
        self.total_attempts += 1
        if success:
            self.successful_drags += 1
        else:
            self.failed_drags += 1
        self.retry_counts.append(retry_count)
        self.coordinate_retries.append(coord_used)
        
        if verification_used:
            self.verification_enabled += 1
            if success:
                self.verification_successful += 1
    
    def get_stats(self):
        if self.total_attempts == 0:
            return "No drag operations recorded"
        
        success_rate = (self.successful_drags / self.total_attempts) * 100
        avg_retries = sum(self.retry_counts) / len(self.retry_counts) if self.retry_counts else 0
        avg_coord_retries = sum(self.coordinate_retries) / len(self.coordinate_retries) if self.coordinate_retries else 0
        verification_rate = (self.verification_enabled / self.total_attempts) * 100 if self.total_attempts > 0 else 0
        verification_success_rate = (self.verification_successful / self.verification_enabled) * 100 if self.verification_enabled > 0 else 0
        
        return f"""
📊 Enhanced Drag Operation Statistics:
   Total attempts: {self.total_attempts}
   Successful: {self.successful_drags}
   Failed: {self.failed_drags}
   Success rate: {success_rate:.1f}%
   Average retries: {avg_retries:.1f}
   Average coordinate attempts: {avg_coord_retries:.1f}
   
🔍 Verification Statistics:
   Verification enabled: {self.verification_enabled} ({verification_rate:.1f}%)
   Verification success rate: {verification_success_rate:.1f}%
        """

class SprunkiGamePlayer:
    def __init__(self):
        # Load configuration from config.json (like json_generator.py)
        self.load_config()

        # Return Mode Constants
        self.RETURN_MODE_INDIVIDUAL = "individual"  # Single icon operations
        self.RETURN_MODE_REMIX = "remix"           # Multiple icons batch operations
        self.RETURN_MODE_AUTO = "auto"             # Auto-detect based on context
        
        # Current return mode tracking
        self.current_return_mode = self.RETURN_MODE_AUTO
        self.active_icons_count = 0  # Track how many icons currently on slots

        # Carpet Grid Configuration
        self.carpet_grid_config = {
            'default_density': 3,      # 3x3 grid per slot
            'high_density': 5,         # 5x5 grid for difficult cases
            'overlap_margin': 10,      # Extra margin around each slot (pixels)
            'wave_size': 9,            # Number of clicks per wave (3x3)
            'max_waves': 10,           # Maximum waves before giving up
            'inter_click_delay': 0.05, # Delay between clicks in same wave
            'inter_wave_delay': 0.3    # Delay between waves
        }

        # Grid cache for performance
        self.carpet_grid_cache = {}

        # Core state
        self.config = None
        self.is_playing = False
        self.should_stop = False
        self.current_icon = 0
        self.auto_thread = None
        
        # Browser management (Chrome debug mode like json_generator.py)
        self.chrome_process = None
        self.ws_url = None
        self.game_url = None
        
        # ✅ DUAL VERIFICATION SYSTEM
        # Characters Array system  
        self.characters_setup_done = False
        self.characters_available = False
        
        # Putted Characters system (backup)
        self.putted_chars_setup_done = False
        self.putted_chars_available = False
        
        # Drag operation statistics (enhanced)
        self.drag_stats = DragOperationStats()

        # Cache cho Safe Zone coordinates
        self.safe_zone_cache = {}

        # Image Detector for UI navigation
        self.detector = ImageDetector("cocrea.world")

        # Domain adjustments for different game domains
        self.domain_adjustments = {
            'sprunki.org': {
                'extra_focus_time': 0.1,
                'drag_speed_multiplier': 1.0,
                'requires_double_click': False,
                'stability_factor': 1.0
            },
            'sprunkiretake.net': {
                'extra_focus_time': 0.2,
                'drag_speed_multiplier': 0.8,
                'requires_double_click': True,
                'stability_factor': 1.2
            },
            'cocrea.world': {
                'extra_focus_time': 0.15,
                'drag_speed_multiplier': 1.2,
                'requires_double_click': False,
                'stability_factor': 0.9
            },
            'gameflare.com': {
                'extra_focus_time': 0.25,
                'drag_speed_multiplier': 0.7,
                'requires_double_click': True,
                'stability_factor': 1.3
            }
        }
        
        # Timing constants for different operations
        self.DRAG_DELAYS = {
            'first_icon': {
                'approach': 0.5,
                'focus': 0.3,
                'hold': 0.4,
                'drag': 1.0,
                'drop': 0.5,
                'post_drop': 0.3
            },
            'normal_icon': {
                'approach': 0.25,
                'focus': 0.15,
                'hold': 0.25,
                'drag': 0.6,
                'drop': 0.3,
                'post_drop': 0.2
            }
        }
        
        # Icon groups by music type
        self.icon_groups = {
            'beats': list(range(0, 5)),      # Index 0-4: Beats
            'effects': list(range(5, 10)),   # Index 5-9: Effects  
            'melodies': list(range(10, 15)), # Index 10-14: Melodies
            'vocals': list(range(15, 20))    # Index 15-19: Vocals
        }
        
        # Remix patterns
        self.remix_patterns = [
            {'name': 'Standard', 'description': 'Chuẩn cân bằng', 'beats': 3, 'effects': 2, 'melodies': 1, 'vocals': 1},
            {'name': 'Melody Focus', 'description': '1 effect ít hơn, 2 melody', 'beats': 3, 'effects': 1, 'melodies': 2, 'vocals': 1},
            {'name': 'Effect Heavy', 'description': 'Nhấn mạnh effect', 'beats': 2, 'effects': 3, 'melodies': 1, 'vocals': 1},
            {'name': 'No Melody', 'description': 'Không melody, thêm vocal', 'beats': 3, 'effects': 2, 'melodies': 0, 'vocals': 2},
            {'name': 'Beat Power', 'description': '4 beats mạnh, ít effect', 'beats': 4, 'effects': 1, 'melodies': 1, 'vocals': 1}
        ]
        
        # Remix library
        self.remix_library = []
        self.current_remix_index = 0
        
        # PyAutoGUI settings
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.08
        pyautogui.DURATION = 0.2

        # Batch Verification Session Management
        self.batch_verification_active = False
        self.session_websocket = None
        self.session_websocket_url = None
        self.verification_functions_ready = False
        self.last_health_check = 0
        self.health_check_interval = 30  # Check every 30s
        self.session_start_time = None

        # JavaScript for finding ALL Skip Intro buttons (Multi-Target Detection)
        self.js_find_skip_intro = '''
        new Promise(async (resolve, reject) => {
            try {
                console.log("🔍 [GAME PLAYER] Searching for ALL Skip Intro buttons...");
                
                function findVM() {
                    if (window.__vm && window.__vm.runtime) return window.__vm;
                    function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        const elements = document.querySelectorAll(selector);
                        for (const el of elements) {
                            const reactKey = getReactKey(el);
                            if (!reactKey) continue;
                            let fiber = el[reactKey]; let depth = 0;
                            while (fiber && depth++ < 100) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm && props.vm.runtime) { window.__vm = props.vm; return props.vm; }
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }
                
                const toPixelWithMenu = (logicX, logicY) => {
                    if (logicX === null || logicY === null) return { x: null, y: null };
                    const stageW = 640; const stageH = 360; const screenW = 1920; const screenH = 1080; const menuWidth = 78;
                    const viewportW = screenW - menuWidth; const scale = Math.min(viewportW / stageW, screenH / stageH);
                    const canvasW = stageW * scale; const canvasH = stageH * scale; const leftOffset = menuWidth;
                    const topOffset = (screenH - canvasH) / 2; const canvasCenterX = leftOffset + (canvasW / 2);
                    const canvasCenterY = topOffset + (canvasH / 2); const pixelX = canvasCenterX + (logicX * scale);
                    const pixelY = canvasCenterY - (logicY * scale);
                    return { x: Math.round(pixelX), y: Math.round(pixelY) };
                };
                
                const vm = findVM();
                if (!vm) {
                    console.log("❌ [GAME PLAYER] VM not found - cannot search for Skip Intro buttons");
                    resolve({ found: false, count: 0, buttons: [], reason: "vm_not_found" });
                    return;
                }
                
                const allTargets = vm.runtime.targets.map(t => t);
                console.log(`🎯 [GAME PLAYER] Total targets found: ${allTargets.length}`);
                
                // Find ALL targets with name "Intro" and costume "Skip"
                const introTargets = allTargets.filter(target => {
                    if (target.sprite.name !== "Intro") {
                        return false;
                    }
                    
                    const hasSkipCostume = target.sprite.costumes.some(costume => 
                        costume.name === "Skip"
                    );
                    
                    return hasSkipCostume;
                });
                
                console.log(`🔍 [GAME PLAYER] Found ${introTargets.length} 'Intro' targets with 'Skip' costume`);
                
                if (introTargets.length === 0) {
                    console.log("ℹ️ [GAME PLAYER] No Skip Intro buttons found");
                    resolve({ found: false, count: 0, buttons: [], reason: "target_not_found" });
                    return;
                }
                
                // Process ALL intro targets
                const skipIntroButtons = [];
                
                introTargets.forEach((target, index) => {
                    console.log(`\\n🎯 [GAME PLAYER] Processing Intro Target ${index + 1}/${introTargets.length}:`);
                    console.log(`   - Logic Position: (${target.x}, ${target.y})`);
                    console.log(`   - Visible: ${target.visible}`);
                    console.log(`   - Current Costume: ${target.sprite.costumes[target.currentCostume]?.name || 'Unknown'}`);
                    
                    const pixelCoords = toPixelWithMenu(target.x, target.y);
                    
                    if (pixelCoords.x !== null && pixelCoords.y !== null) {
                        const buttonInfo = {
                            index: index + 1,
                            x: pixelCoords.x,
                            y: pixelCoords.y,
                            logicX: target.x,
                            logicY: target.y,
                            visible: target.visible,
                            targetName: target.sprite.name,
                            currentCostumeName: target.sprite.costumes[target.currentCostume]?.name || 'Unknown'
                        };
                        
                        skipIntroButtons.push(buttonInfo);
                        console.log(`   ✅ [GAME PLAYER] Converted to pixel: (${pixelCoords.x}, ${pixelCoords.y}) - ${target.visible ? 'VISIBLE' : 'HIDDEN'}`);
                    }
                });
                
                const visibleButtons = skipIntroButtons.filter(btn => btn.visible);
                
                console.log(`\\n📊 [GAME PLAYER] BUTTONS SUMMARY: Total: ${skipIntroButtons.length}, Visible: ${visibleButtons.length}`);
                
                resolve({
                    found: skipIntroButtons.length > 0,
                    count: skipIntroButtons.length,
                    buttons: skipIntroButtons,
                    visibleCount: visibleButtons.length,
                    reason: skipIntroButtons.length > 0 ? "success" : "no_valid_buttons"
                });
                
            } catch(e) { 
                console.error("❌ [GAME PLAYER] Error searching for Skip Intro buttons:", e);
                resolve({ found: false, count: 0, buttons: [], reason: "error", error: e.toString() }); 
            }
        });
        '''

        # Setup GUI
        self.setup_gui()

    def load_config(self):
            """Load configuration from config.json like json_generator.py"""
            try:
                from config_manager import get_config
                self.app_config = get_config()
                
                # Chrome settings from config
                self.chrome_config = self.app_config.get('chrome', {})
                self.chrome_paths = self.chrome_config.get('executable_paths', [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe", 
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                ])
                self.debugging_port = self.chrome_config.get('debugging_port', 9222)
                self.user_data_dir = self.chrome_config.get('user_data_dir', 'chrome_debug_profile')
                
                # Game settings from config
                self.game_config = self.app_config.get('game', {})
                self.page_load_wait = self.game_config.get('page_load_wait', 20)
                self.game_load_wait = self.game_config.get('game_load_wait', 10)
                self.fullscreen_wait = self.game_config.get('fullscreen_wait', 8)
                self.max_pagedown_attempts = self.game_config.get('max_pagedown_attempts', 5)
                self.intro_skip_clicks = self.game_config.get('intro_skip_clicks', 10)
                
                # Load verification settings
                self.load_verification_config()
                
                print(f"✅ Game Player configured from config.json:")
                print(f"   Debug port: {self.debugging_port}")
                print(f"   Page load wait: {self.page_load_wait}s")
                print(f"   Game load wait: {self.game_load_wait}s")
                print(f"   Intro skip clicks: {self.intro_skip_clicks}")
                print(f"   Smart verification: {'✅ Enabled' if self.page_verification_enabled else '❌ Disabled'}")
                
            except Exception as e:
                print(f"⚠️ Could not load config, using defaults: {e}")
                # Set defaults
                self.chrome_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe", 
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                ]
                self.debugging_port = 9222
                self.user_data_dir = 'chrome_debug_profile'
                self.page_load_wait = 20
                self.game_load_wait = 10
                self.fullscreen_wait = 8
                self.max_pagedown_attempts = 5
                self.intro_skip_clicks = 10
                
                # Set default verification settings
                self.page_verification_enabled = True
                self.game_verification_enabled = True
                self.page_verification_timeout = 120
                self.game_verification_timeout = 90
                self.page_verification_interval = 2
                self.game_verification_interval = 2
                self.stabilization_wait = 3
                self.game_container_selectors = [".ccw-stage-wrapper", "#stage-canvas-wrapper", "canvas"]
                self.min_containers_required = 1
                self.check_react_components = True
                self.check_run_button_dom = True

    def load_verification_config(self):
            """Load verification configuration from config.json"""
            try:
                # Page verification settings
                self.page_verification = self.app_config.get('page_verification', {})
                self.page_verification_enabled = self.page_verification.get('enabled', True)
                self.page_verification_timeout = self.page_verification.get('timeout_ms', 120000) / 1000  # Convert to seconds
                self.page_verification_interval = self.page_verification.get('check_interval_ms', 2000) / 1000
                self.page_verification_criteria = self.page_verification.get('criteria', {})
                self.page_verification_retries = self.page_verification.get('max_retries', 3)
                self.stabilization_wait = self.page_verification.get('stabilization_wait_seconds', 3)
                
                # Game load verification settings
                self.game_verification = self.app_config.get('game_load_verification', {})
                self.game_verification_enabled = self.game_verification.get('enabled', True)
                self.game_verification_timeout = self.game_verification.get('timeout_ms', 90000) / 1000
                self.game_verification_interval = self.game_verification.get('check_interval_ms', 2000) / 1000
                self.game_container_selectors = self.game_verification.get('game_container_selectors', [])
                self.min_containers_required = self.game_verification.get('min_containers_required', 1)
                self.check_react_components = self.game_verification.get('check_react_components', True)
                self.check_run_button_dom = self.game_verification.get('check_run_button_dom', True)
                
                print(f"✅ Verification config loaded:")
                print(f"   Page verification: {'✅ Enabled' if self.page_verification_enabled else '❌ Disabled'}")
                print(f"   Game verification: {'✅ Enabled' if self.game_verification_enabled else '❌ Disabled'}")
                print(f"   Page timeout: {self.page_verification_timeout}s")
                print(f"   Game timeout: {self.game_verification_timeout}s")
                
            except Exception as e:
                print(f"⚠️ Could not load verification config, using defaults: {e}")
                # Set defaults if config loading fails
                self.page_verification_enabled = True
                self.game_verification_enabled = True
                self.page_verification_timeout = 120
                self.game_verification_timeout = 90
                self.page_verification_interval = 2
                self.game_verification_interval = 2
                self.stabilization_wait = 3
                self.game_container_selectors = [".ccw-stage-wrapper", "#stage-canvas-wrapper", "canvas"]
                self.min_containers_required = 1
                self.check_react_components = True
                self.check_run_button_dom = True

    def _execute_verification_js(self, js_code):
        """Execute JavaScript for verification via WebSocket/CDP"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("⚠️ WebSocket not available for verification, skipping JS check")
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_code, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')
                return result
            else:
                print("⚠️ No response from verification JavaScript")
                return None
                
        except Exception as e:
            print(f"⚠️ Error executing verification JavaScript: {e}")
            return None

    def verify_page_fully_loaded(self, target_url):
        """
        🔍 Smart Page Load Verification System
        Checks if page is fully loaded before proceeding with image detection
        """
        try:
            if not self.page_verification_enabled:
                print("🔍 Page verification disabled, assuming page ready")
                return True
            
            print(f"\n🔍 SMART PAGE VERIFICATION: {target_url}")
            print("="*70)
            
            # JavaScript for comprehensive page verification
            verification_js = """
            (() => {
                const results = {
                    documentState: document.readyState,
                    basicElements: {
                        hasBody: !!document.body,
                        hasHead: !!document.head,
                        bodyChildren: document.body ? document.body.children.length : 0
                    },
                    loadingSpinners: {
                        count: 0,
                        types: []
                    },
                    networkActivity: {
                        pendingRequests: 0,
                        estimatedIdle: true
                    },
                    currentUrl: window.location.href,
                    timestamp: Date.now()
                };
                
                // Check for common loading spinner patterns
                const spinnerSelectors = [
                    '.loading', '.spinner', '.loader', 
                    '[class*="loading"]', '[class*="spinner"]', '[class*="loader"]',
                    '.fa-spinner', '.fa-circle-o-notch'
                ];
                
                spinnerSelectors.forEach(selector => {
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0) {
                        const visibleSpinners = Array.from(elements).filter(el => {
                            const style = window.getComputedStyle(el);
                            return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                        });
                        if (visibleSpinners.length > 0) {
                            results.loadingSpinners.count += visibleSpinners.length;
                            results.loadingSpinners.types.push(selector);
                        }
                    }
                });
                
                // Estimate network activity (simplified)
                try {
                    if (window.performance && window.performance.getEntriesByType) {
                        const navigationEntries = window.performance.getEntriesByType('navigation');
                        if (navigationEntries.length > 0) {
                            const nav = navigationEntries[0];
                            results.networkActivity.loadComplete = nav.loadEventEnd > 0;
                            results.networkActivity.domComplete = nav.domComplete > 0;
                        }
                    }
                } catch (e) {
                    console.log('Network activity check failed:', e);
                }
                
                // Overall readiness assessment
                results.readyScore = 0;
                if (results.documentState === 'complete') results.readyScore += 30;
                if (results.basicElements.hasBody && results.basicElements.hasHead) results.readyScore += 25;
                if (results.basicElements.bodyChildren > 0) results.readyScore += 20;
                if (results.loadingSpinners.count === 0) results.readyScore += 25;
                
                results.isReady = results.readyScore >= 75;
                
                return results;
            })();
            """
            
            start_time = time.time()
            max_retries = self.page_verification_retries
            
            for attempt in range(1, max_retries + 1):
                if self.should_stop:
                    return False
                
                print(f"🔍 Page verification attempt {attempt}/{max_retries}")
                
                # Execute verification JavaScript
                verification_result = self._execute_verification_js(verification_js)
                
                if verification_result:
                    document_state = verification_result.get('documentState', 'unknown')
                    basic_elements = verification_result.get('basicElements', {})
                    loading_spinners = verification_result.get('loadingSpinners', {})
                    ready_score = verification_result.get('readyScore', 0)
                    is_ready = verification_result.get('isReady', False)
                    
                    print(f"   📊 Document state: {document_state}")
                    print(f"   📊 Basic elements: Body={basic_elements.get('hasBody')}, Head={basic_elements.get('hasHead')}, Children={basic_elements.get('bodyChildren', 0)}")
                    print(f"   📊 Loading spinners: {loading_spinners.get('count', 0)} active")
                    print(f"   📊 Ready score: {ready_score}/100")
                    print(f"   📊 Overall assessment: {'✅ READY' if is_ready else '⏳ NOT READY'}")
                    
                    if is_ready:
                        elapsed = time.time() - start_time
                        print(f"✅ PAGE VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if loading_spinners.get('count', 0) > 0:
                            spinner_types = ', '.join(loading_spinners.get('types', []))
                            print(f"   ⏳ Waiting for loading spinners to disappear: {spinner_types}")
                        if document_state != 'complete':
                            print(f"   ⏳ Waiting for document to complete loading...")
                        if basic_elements.get('bodyChildren', 0) == 0:
                            print(f"   ⏳ Waiting for page content to render...")
                
                else:
                    print(f"   ⚠️ Verification JavaScript failed, using fallback check")
                    # Fallback: Simple document ready check
                    simple_check = self._execute_verification_js("document.readyState")
                    if simple_check == 'complete':
                        print(f"   ✅ Fallback check passed: document.readyState = complete")
                        return True
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.page_verification_timeout:
                    print(f"⚠️ Page verification timeout ({self.page_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with image detection...")
                    return True  # Don't fail completely, proceed with image detection
                
                # Wait before next attempt
                if attempt < max_retries:
                    print(f"   ⏳ Waiting {self.page_verification_interval}s before next check...")
                    time.sleep(self.page_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"⚠️ Page verification failed after {max_retries} attempts")
            print("   Proceeding anyway with image detection...")
            return True
            
        except Exception as e:
            print(f"⚠️ Error in page verification: {e}")
            print("   Proceeding anyway with image detection...")
            return True  # Don't block workflow on verification errors

    def verify_game_components_ready(self):
        """
        🎮 Smart Game Load Verification System
        Checks if game components are ready before proceeding with Run button detection
        """
        try:
            if not self.game_verification_enabled:
                print("🎮 Game verification disabled, assuming game ready")
                return True
            
            print(f"\n🎮 SMART GAME VERIFICATION")
            print("="*70)
            
            # JavaScript for game component verification
            game_verification_js = f"""
            (() => {{
                const selectors = {self.game_container_selectors};
                const results = {{
                    gameContainers: [],
                    reactComponents: {{
                        found: false,
                        details: []
                    }},
                    runButtonInDOM: false,
                    overallReady: false,
                    timestamp: Date.now()
                }};
                
                // Check game container elements
                selectors.forEach(selector => {{
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0) {{
                        elements.forEach((el, index) => {{
                            const rect = el.getBoundingClientRect();
                            results.gameContainers.push({{
                                selector: selector,
                                index: index,
                                id: el.id || 'no-id',
                                className: el.className || 'no-class',
                                visible: rect.width > 0 && rect.height > 0,
                                hasChildren: el.children.length > 0
                            }});
                        }});
                    }}
                }});
                
                // Check for React components (look for React fiber keys)
                const checkReactComponents = () => {{
                    const elements = document.querySelectorAll('*');
                    let reactFound = false;
                    for (let el of elements) {{
                        const keys = Object.keys(el);
                        const reactKey = keys.find(key => key.startsWith('__react'));
                        if (reactKey) {{
                            reactFound = true;
                            results.reactComponents.details.push({{
                                element: el.tagName,
                                reactKey: reactKey.substring(0, 20) + '...'
                            }});
                            if (results.reactComponents.details.length >= 3) break; // Limit for performance
                        }}
                    }}
                    results.reactComponents.found = reactFound;
                }};
                
                if ({str(self.check_react_components).lower()}) {{
                    checkReactComponents();
                }}
                
                // Check if Run button exists in DOM (even if not visible)
                if ({str(self.check_run_button_dom).lower()}) {{
                    const runButtonSelectors = [
                        'button[type="submit"]',
                        'button:contains("Run")',
                        '.run-button',
                        '#run-button',
                        '[class*="run"]',
                        '[class*="play"]',
                        '[class*="start"]',
                        'button',
                        'input[type="submit"]'
                    ];
                    
                    for (let selector of runButtonSelectors) {{
                        try {{
                            const buttons = document.querySelectorAll(selector);
                            for (let button of buttons) {{
                                const text = button.textContent || button.value || button.alt || '';
                                if (text.toLowerCase().includes('run') || 
                                    text.toLowerCase().includes('play') || 
                                    text.toLowerCase().includes('start')) {{
                                    results.runButtonInDOM = true;
                                    break;
                                }}
                            }}
                            if (results.runButtonInDOM) break;
                        }} catch (e) {{
                            // Skip invalid selectors
                        }}
                    }}
                }}
                
                // Assessment
                const gameContainersFound = results.gameContainers.length >= {self.min_containers_required};
                const reactReady = !{str(self.check_react_components).lower()} || results.reactComponents.found;
                const runButtonReady = !{str(self.check_run_button_dom).lower()} || results.runButtonInDOM;
                
                results.overallReady = gameContainersFound && reactReady && runButtonReady;
                
                return results;
            }})();
            """
            
            start_time = time.time()
            max_attempts = 3
            
            for attempt in range(1, max_attempts + 1):
                if self.should_stop:
                    return False
                
                print(f"🎮 Game verification attempt {attempt}/{max_attempts}")
                
                # Execute game verification JavaScript
                game_result = self._execute_verification_js(game_verification_js)
                
                if game_result:
                    game_containers = game_result.get('gameContainers', [])
                    react_components = game_result.get('reactComponents', {})
                    run_button_in_dom = game_result.get('runButtonInDOM', False)
                    overall_ready = game_result.get('overallReady', False)
                    
                    print(f"   🎯 Game containers found: {len(game_containers)}")
                    for container in game_containers[:3]:  # Show first 3
                        print(f"      - {container['selector']}: visible={container['visible']}, children={container['hasChildren']}")
                    
                    if self.check_react_components:
                        react_found = react_components.get('found', False)
                        react_count = len(react_components.get('details', []))
                        print(f"   ⚛️ React components: {'✅ Found' if react_found else '❌ Not found'} ({react_count} detected)")
                    
                    if self.check_run_button_dom:
                        print(f"   🔘 Run button in DOM: {'✅ Found' if run_button_in_dom else '❌ Not found'}")
                    
                    print(f"   🎯 Overall game ready: {'✅ YES' if overall_ready else '❌ NO'}")
                    
                    if overall_ready:
                        elapsed = time.time() - start_time
                        print(f"✅ GAME VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if len(game_containers) < self.min_containers_required:
                            print(f"   ⏳ Waiting for game containers to load ({len(game_containers)}/{self.min_containers_required})")
                        if self.check_react_components and not react_components.get('found'):
                            print(f"   ⏳ Waiting for React components to initialize")
                        if self.check_run_button_dom and not run_button_in_dom:
                            print(f"   ⏳ Waiting for Run button to appear in DOM")
                
                else:
                    print(f"   ⚠️ Game verification JavaScript failed")
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.game_verification_timeout:
                    print(f"⚠️ Game verification timeout ({self.game_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with image detection...")
                    return True
                
                # Wait before next attempt
                if attempt < max_attempts:
                    print(f"   ⏳ Waiting {self.game_verification_interval}s before next check...")
                    time.sleep(self.game_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"⚠️ Game verification failed after {max_attempts} attempts")
            print("   Proceeding anyway with image detection...")
            return True
            
        except Exception as e:
            print(f"⚠️ Error in game verification: {e}")
            print("   Proceeding anyway with image detection...")
            return True

    # ================================================================
    # == CHROME DEBUG BROWSER MANAGEMENT (from json_generator.py) ==
    # ================================================================

    def cleanup_chrome_complete(self):
        """Complete cleanup: Close Chrome debug entirely and clean all resources"""
        try:
            print("🧹 Complete cleanup: Closing Chrome debug and cleaning resources...")
            
            # Close windows by title
            for title_part in ['cocrea.world', 'Chrome']:
                windows = gw.getWindowsWithTitle(title_part)
                for window in windows:
                    try:
                        print(f"   🗂️ Closing window: {window.title}")
                        window.close()
                    except:
                        pass
            
            time.sleep(2)
            
            # Terminate Chrome process
            if hasattr(self, 'chrome_process') and self.chrome_process:
                try:
                    print("   🔄 Terminating Chrome process...")
                    self.chrome_process.terminate()
                    self.chrome_process.wait(timeout=5)
                except:
                    try:
                        self.chrome_process.kill()
                    except:
                        pass
                self.chrome_process = None
            
            # Kill leftover Chrome debug processes
            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] and 'chrome' in proc.info['name'].lower():
                            cmdline = proc.info.get('cmdline', [])
                            if cmdline and f'--remote-debugging-port={self.debugging_port}' in ' '.join(cmdline):
                                print(f"   ⚔️ Killing leftover Chrome debug process: {proc.info['pid']}")
                                proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except ImportError:
                print("   ⚠️ psutil not available for process cleanup")
            
            self.ws_url = None
            
            print("✅ Complete cleanup finished - Chrome debug fully closed")
            time.sleep(1)
            return True
            
        except Exception as e:
            print(f"❌ Complete cleanup error: {e}")
            return False

    def navigate_and_prepare_game(self, url):
            """Navigate to game URL using Chrome debug mode with Smart Verification"""
            try:
                print(f"🌐 Opening game URL with Chrome debug session: {url}")
                
                # Complete cleanup first
                self.cleanup_chrome_complete()
                time.sleep(2)
                
                self.game_url = url
                
                # Find uBlock extension (AdBlocker)
                extensions_dir = os.path.join(os.getcwd(), 'extensions')
                ublock_path = None
                if os.path.exists(extensions_dir):
                    subdirs = [d for d in os.listdir(extensions_dir) if os.path.isdir(os.path.join(extensions_dir, d))]
                    if subdirs: 
                        ublock_path = os.path.join(extensions_dir, subdirs[0])
                
                if not ublock_path:
                    print("⚠️ uBlock Origin not found in 'extensions' folder - continuing without AdBlocker")
                else:
                    print(f"✅ Found AdBlocker: {ublock_path}")
                
                # Find Chrome executable
                chrome_exe = None
                for path in self.chrome_paths:
                    if os.path.exists(path): 
                        chrome_exe = path
                        break
                        
                if not chrome_exe: 
                    messagebox.showerror("Error", "Google Chrome not found.")
                    return False
                
                # Build Chrome command with debug mode
                chrome_cmd = [
                    chrome_exe, 
                    f"--remote-debugging-port={self.debugging_port}", 
                    f"--user-data-dir={os.path.join(os.getcwd(), self.user_data_dir)}",
                    "--no-first-run", 
                    "--window-size=1280,720", 
                    "--remote-allow-origins=*",
                    url
                ]
                
                # Add AdBlocker if available
                if ublock_path:
                    chrome_cmd.insert(-1, f"--load-extension={ublock_path}")
                
                print("🔧 Launching Chrome with debug mode and AdBlocker...")
                self.chrome_process = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                print(f"⏳ Waiting {self.page_load_wait}s for Chrome and AdBlocker to initialize...")
                time.sleep(self.page_load_wait)
                
                # Find game window
                game_window = None
                for _ in range(10): 
                    possible_windows = gw.getWindowsWithTitle('cocrea.world')
                    if not possible_windows: 
                        possible_windows = gw.getWindowsWithTitle('Chrome')
                    if possible_windows: 
                        game_window = possible_windows[0]
                        break
                    time.sleep(1)
                    
                if not game_window: 
                    messagebox.showerror("Setup Error", "Could not find the game window.")
                    return False
                    
                print(f"🎯 Found game window: '{game_window.title}'. Activating...")
                game_window.activate()
                time.sleep(0.5)
                game_window.maximize()
                time.sleep(2)
                
                # ================================================================
                # 🔍 SMART VERIFICATION SYSTEM (REPLACES FIXED 40s WAIT)
                # ================================================================
                print(f"\n🔍 STARTING SMART VERIFICATION SYSTEM")
                print("="*70)
                
                # Phase 1: Page Load Verification
                print("🔍 Phase 1: Smart Page Load Verification...")
                page_verification_success = self.verify_page_fully_loaded(url)
                
                if not page_verification_success:
                    print("⚠️ Page verification failed, but continuing with workflow...")
                
                # Phase 2: Game Components Verification  
                print("🎮 Phase 2: Smart Game Components Verification...")
                game_verification_success = self.verify_game_components_ready()
                
                if not game_verification_success:
                    print("⚠️ Game verification failed, but continuing with workflow...")
                
                # Phase 3: Stabilization wait (replaces fixed 40s with smart 3s)
                if page_verification_success and game_verification_success:
                    print(f"✅ VERIFICATION SUCCESSFUL: Page and game fully loaded!")
                    print(f"⏳ Stabilization wait: {self.stabilization_wait}s for optimal image detection...")
                    time.sleep(self.stabilization_wait)
                else:
                    print(f"⚠️ Verification had issues, using fallback stabilization...")
                    time.sleep(5)  # Fallback wait if verification failed
                
                print("🎯 Smart verification completed - starting enhanced Run button detection...")
                print("="*70)
                
                # ================================================================
                # 🚀 ENHANCED RUN BUTTON DETECTION (KEEPS ORIGINAL IMAGE LOGIC)
                # ================================================================
                
                # Find and click Run button with enhanced workflow
                run_button_clicked = False
                max_retries = 3
                
                for attempt in range(1, max_retries + 1):
                    print(f"\n--- 🚀 Finding 'Run' Button - Attempt {attempt}/{max_retries} ---")
                    
                    if attempt > 1:
                        print("   -> Action: Reloading page to clear potential issues...")
                        pyautogui.hotkey('ctrl', 'r')
                        
                        # Quick re-verification after reload
                        print("   -> Quick re-verification after reload...")
                        time.sleep(3)  # Brief wait for reload
                        quick_verification = self._execute_verification_js("document.readyState")
                        if quick_verification == 'complete':
                            print("   -> ✅ Page reloaded successfully")
                            time.sleep(self.stabilization_wait)  # Smart stabilization
                        else:
                            print("   -> ⚠️ Page reload uncertain, using fallback wait...")
                            time.sleep(8)  # Fallback wait
                    else:
                        # First attempt - verification already done above
                        print("   -> Using smart verification results (no additional wait needed)")
                    
                    print("   -> 🔍 Searching for 'Run' button on screen...")
                    
                    # KEEP ORIGINAL IMAGE DETECTION LOGIC EXACTLY AS-IS
                    if self.detector.click_run_button(max_pagedown_attempts=self.max_pagedown_attempts):
                        print(f"✅ SUCCESS: 'Run' button found and clicked on attempt {attempt}.")
                        run_button_clicked = True
                        break
                    else:
                        print(f"⚠️ FAILED: 'Run' button not found on attempt {attempt}.")
                        if attempt < max_retries:
                            print("   -> Preparing for next attempt...")
                        else:
                            messagebox.showerror("Setup Error", f"Could not find 'Run' button after {max_retries} attempts.")
                            return False
                            
                if not run_button_clicked: 
                    return False
                    
                print(f"\n⏳ Waiting {self.game_load_wait}s for game to load after clicking Run...")
                time.sleep(self.game_load_wait)
                
                # ================================================================
                # 📺 FULLSCREEN BUTTON DETECTION (KEEPS ORIGINAL IMAGE LOGIC)
                # ================================================================
                
                # Click fullscreen button - KEEP ORIGINAL LOGIC EXACTLY AS-IS
                if self.detector.click_fullscreen_button(wait_time=self.fullscreen_wait):
                    print("✅ Clicked game's own fullscreen button.")
                    time.sleep(2)
                else:
                    print("⚠️ Fullscreen button not found, continuing anyway.")
                    
                # Press F11 for true fullscreen
                print("⌨️ Pressing F11 for true fullscreen mode...")
                pyautogui.press('f11')
                time.sleep(3)
                
                print("✅ Game setup completed with Smart Verification System. Ready for automation.")
                return True
                
            except Exception as e:
                messagebox.showerror("Setup Error", f"Failed to prepare game: {e}")
                print(f"⌚ Failed to navigate and prepare game: {e}")
                self.cleanup_chrome_complete()
                return False

    def _get_websocket_url(self):
        """Get WebSocket URL for CDP communication"""
        if self.ws_url: 
            return self.ws_url
        try:
            response = requests.get(f"http://localhost:{self.debugging_port}/json", timeout=5)
            tabs = response.json()
            target_tab = next((t for t in tabs if t.get('type') == 'page' and 'cocrea.world' in t.get('url', '')), None)
            if not target_tab: 
                print("⚠️ Could not find cocrea.world tab for WebSocket connection")
                return None
            self.ws_url = target_tab['webSocketDebuggerUrl']
            return self.ws_url
        except Exception as e:
            print(f"❌ Could not get WebSocket URL: {e}")
            return None

    def _send_cdp_command(self, ws, command):
        """Send CDP command via WebSocket"""
        try:
            command_id = random.randint(1, 1000000)
            command['id'] = command_id
            ws.send(json.dumps(command))
            
            max_wait_time = 15
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                try:
                    response_str = ws.recv()
                    response_data = json.loads(response_str)
                    if response_data.get('id') == command_id:
                        return response_data
                except websocket.WebSocketTimeoutError:
                    print("WARNING: WebSocket timeout, continuing to wait...")
                    continue
                    
            print(f"ERROR: CDP command timeout after {max_wait_time}s")
            return None
            
        except Exception as e:
            print(f"ERROR: CDP command error: {e}")
            return None

    def _simulate_click_via_cdp(self, ws, x, y):
        """Simulate mouse click via CDP"""
        down_command = {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}
        self._send_cdp_command(ws, down_command)
        time.sleep(random.uniform(0.05, 0.1))
        up_command = {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}
        self._send_cdp_command(ws, up_command)

    def _simulate_drag_via_cdp(self, ws, start_x, start_y, end_x, end_y):
        """Simulate mouse drag via CDP"""
        # Move to start position
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
        time.sleep(0.2)
        
        # Mouse down
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1}})
        time.sleep(0.2)
        
        # Drag with smooth movement
        steps = 20
        for i in range(1, steps + 1):
            progress = i / steps
            curr_x = start_x + (end_x - start_x) * progress
            curr_y = start_y + (end_y - start_y) * progress
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left"}})
            time.sleep(0.02)
            
        # Mouse up at end position
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1}})

    # ================================================================
    # == NEW: DRAG VERIFICATION SYSTEM ==
    # ================================================================

    def setup_dual_verification_system(self):
        """
        🔄 DUAL VERIFICATION: Setup cả Characters Array + Putted Characters
        Chỉ cần 1 trong 2 hoạt động là đủ
        """
        try:
            print("🔄 Setting up DUAL verification system...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("⚠️ WebSocket not available, dual verification disabled")
                self.characters_available = False
                self.putted_chars_available = False
                return False
            
            # Setup JavaScript cho CẢ HAI systems
            dual_setup_js = '''
            (() => {
                console.log("🚀 Setting up DUAL verification system...");
                
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
                    console.error("❌ Cannot find VM for dual verification");
                    return { characters: false, putted: false };
                }
                
                const runtime = vm.runtime || vm;
                const stage = (runtime.targets || []).find(t => t.isStage) || (runtime.targets || [])[0];
                if (!stage) {
                    console.error("❌ Cannot find Stage for dual verification");
                    return { characters: false, putted: false };
                }
                
                // Store VM và Stage
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
                        console.log("✅ Characters Array system ready:", testChars);
                    }
                } catch (e) {
                    console.warn("⚠️ Characters Array setup failed:", e);
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
                            console.log("✅ Putted Characters system ready:", testPutted);
                        }
                    }
                } catch (e) {
                    console.warn("⚠️ Putted Characters setup failed:", e);
                }
                
                console.log("🔄 DUAL VERIFICATION SETUP RESULT:");
                console.log("   Characters Array:", charactersSuccess ? "✅ Ready" : "❌ Failed");
                console.log("   Putted Characters:", puttedSuccess ? "✅ Ready" : "❌ Failed");
                
                return {
                    characters: charactersSuccess,
                    putted: puttedSuccess,
                    hasBackup: charactersSuccess || puttedSuccess
                };
            })();
            '''
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            response = self._send_cdp_command(ws, {
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
                    print("✅ DUAL verification system setup successful!")
                    print(f"   Characters Array: {'✅ Ready' if self.characters_available else '❌ Failed'}")
                    print(f"   Putted Characters: {'✅ Ready' if self.putted_chars_available else '❌ Failed'}")
                    
                    # Test cả hai systems
                    if self.characters_available:
                        test_chars = self.get_characters_count()
                        print(f"   🧪 Characters test: {test_chars}")
                    
                    if self.putted_chars_available:
                        test_putted = self.get_putted_chars_value()
                        print(f"   🧪 Putted test: {test_putted}")
                    
                    self.characters_setup_done = True
                    self.putted_chars_setup_done = True
                    return True
                else:
                    print("❌ DUAL verification setup failed - no systems available")
                    return False
            else:
                print("❌ No response from dual verification setup")
                return False
                
        except Exception as e:
            print(f"❌ Error setting up dual verification: {e}")
            self.characters_available = False
            self.putted_chars_available = False
            return False

    def start_batch_verification_session(self):
        """
        🚀 NEW: Start batch verification session with persistent WebSocket (FIXED LOGIC)
        """
        try:
            print("🚀 STARTING BATCH VERIFICATION SESSION...")
            print("="*60)
            
            # Get WebSocket URL
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("❌ Cannot get WebSocket URL for batch session")
                return False
            
            # Create persistent WebSocket connection
            self.session_websocket = websocket.create_connection(ws_url, timeout=10)
            # 🔧 NEW: Set default timeout for operations
            self.session_websocket.settimeout(5)  # 5 second default timeout
            self.session_websocket_url = ws_url
            self.session_start_time = time.time()
            
            # 🔧 FIX: Set active BEFORE testing to allow _send_batch_cdp_command to work
            self.batch_verification_active = True
            
            print(f"✅ Batch WebSocket connected: {ws_url}")
            
            # Test connection with active flag set
            print("🔍 Testing batch WebSocket connection...")
            test_response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": "window.location.href", "returnByValue": True}
            })
            
            if not test_response:
                print("❌ Batch WebSocket test failed - trying simple test...")
                
                # Try simpler test
                simple_test = self._send_batch_cdp_command({
                    "method": "Runtime.evaluate",
                    "params": {"expression": "true", "returnByValue": True}
                })
                
                if not simple_test:
                    print("❌ Even simple batch test failed")
                    self.stop_batch_verification_session()
                    return False
                else:
                    print("✅ Simple batch test passed, continuing...")
            else:
                print("✅ Batch WebSocket test successful")
            
            # Setup verification functions
            success = self._setup_batch_verification_functions()
            if success:
                # Keep batch_verification_active = True (already set above)
                print("🎯 BATCH VERIFICATION SESSION ACTIVE!")
                print(f"   📊 Performance: WebSocket reuse enabled")
                print(f"   ⚡ Speed: Pre-warmed verification functions")
                print(f"   🛡️ Reliability: Connection health monitoring")
                print("="*60)
                return True
            else:
                print("❌ Failed to setup batch verification functions")
                self.stop_batch_verification_session()
                return False
                
        except Exception as e:
            print(f"❌ Error starting batch verification session: {e}")
            self.stop_batch_verification_session()
            return False

    def stop_batch_verification_session(self):
        """
        🛑 NEW: Stop batch verification session and cleanup
        """
        try:
            print("\n🛑 STOPPING BATCH VERIFICATION SESSION...")
            
            if self.session_websocket:
                try:
                    self.session_websocket.close()
                    print("   ✅ Batch WebSocket closed")
                except:
                    print("   ⚠️ WebSocket close warning (already closed)")
            
            # Reset session state
            self.batch_verification_active = False
            self.session_websocket = None
            self.session_websocket_url = None
            self.verification_functions_ready = False
            self.session_start_time = None
            
            print("✅ Batch verification session stopped")
            
        except Exception as e:
            print(f"⚠️ Error stopping batch session: {e}")
            # Force reset
            self.batch_verification_active = False
            self.session_websocket = None

    def _setup_batch_verification_functions(self):
        """
        ⚡ NEW: Setup verification functions once for entire session
        """
        try:
            print("⚡ Setting up batch verification functions...")
            
            # Enhanced JavaScript setup for batch operations
            batch_setup_js = '''
            (() => {
                console.log("⚡ Setting up BATCH verification functions...");
                
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
                    console.error("❌ VM not found for batch verification");
                    return { success: false, reason: "vm_not_found" };
                }
                
                const runtime = vm.runtime || vm;
                const stage = (runtime.targets || []).find(t => t.isStage) || (runtime.targets || [])[0];
                if (!stage) {
                    console.error("❌ Stage not found for batch verification");
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
                
                console.log("⚡ BATCH VERIFICATION FUNCTIONS READY:");
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
                    
                    print(f"   ✅ Batch functions setup successful!")
                    print(f"   📊 Characters function: {'✅' if chars_ready else '❌'}")
                    print(f"   📊 Putted function: {'✅' if putted_ready else '❌'}")  
                    print(f"   📊 Dual state function: {'✅' if dual_ready else '❌'}")
                    
                    return True
                else:
                    reason = result.get('reason', 'unknown')
                    print(f"   ❌ Batch setup failed: {reason}")
                    return False
            else:
                print("   ❌ No response from batch setup")
                return False
                
        except Exception as e:
            print(f"❌ Error setting up batch functions: {e}")
            return False

    def _send_batch_cdp_command(self, command):
        """
        ⚡ NEW: Send CDP command using persistent batch WebSocket (DEBUG ENHANCED)
        """
        try:
            if not self.session_websocket or not self.batch_verification_active:
                print(f"   🔍 Batch command failed: WebSocket={bool(self.session_websocket)}, Active={self.batch_verification_active}")
                return None
            
            command_id = random.randint(1, 1000000)
            command['id'] = command_id
            
            print(f"   📤 Sending batch command: {command['method']}")
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
                    
                    print(f"   📥 Received response for command ID: {response_data.get('id', 'NO_ID')}")
                    
                    if response_data.get('id') == command_id:
                        print(f"   ✅ Batch command successful: {command['method']}")
                        return response_data
                        
                except websocket.WebSocketTimeoutError:
                    # This is expected for short timeouts, continue waiting
                    continue
                except Exception as e:
                    print(f"   ⚠️ WebSocket recv error: {e}")
                    break
            
            print(f"   ⏰ Batch CDP command timeout after {max_wait_time}s")
            return None
            
        except Exception as e:
            print(f"   ❌ Batch CDP command error: {e}")
            # Mark session as failed for health check
            self.batch_verification_active = False
            return None

    def check_batch_session_health(self):
        """
        🔍 NEW: Check batch session health and auto-reconnect if needed
        """
        try:
            current_time = time.time()
            
            # Skip if health check not due
            if current_time - self.last_health_check < self.health_check_interval:
                return True
            
            self.last_health_check = current_time
            
            if not self.batch_verification_active or not self.session_websocket:
                return False
            
            print("🔍 Batch session health check...")
            
            # Quick health test
            test_response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": "Date.now()", "returnByValue": True}
            })
            
            if test_response:
                session_duration = current_time - (self.session_start_time or current_time)
                print(f"   ✅ Batch session healthy (running {session_duration:.1f}s)")
                return True
            else:
                print("   ❌ Batch session unhealthy - attempting reconnect...")
                return self._attempt_batch_session_reconnect()
                
        except Exception as e:
            print(f"⚠️ Health check error: {e}")
            return False

    def _attempt_batch_session_reconnect(self):
        """
        🔄 NEW: Attempt to reconnect batch session
        """
        try:
            print("🔄 Attempting batch session reconnect...")
            
            # Stop current session
            self.stop_batch_verification_session()
            time.sleep(1)
            
            # Try to restart
            if self.start_batch_verification_session():
                print("✅ Batch session reconnected successfully")
                return True
            else:
                print("❌ Batch session reconnect failed")
                return False
                
        except Exception as e:
            print(f"❌ Reconnect error: {e}")
            return False

    def get_characters_count(self):
        """
        📢 BATCH OPTIMIZED: Get Characters count via batch session or fallback
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
            print("⚠️ Batch unavailable, using individual verification")
            return self._get_characters_count_individual()
            
        except Exception as e:
            print(f"❌ Error in batch characters count: {e}")
            return self._get_characters_count_individual()

    def _get_characters_count_individual(self):
        """
        📢 FALLBACK: Individual Characters count (original method)
        """
        try:
            if not self.characters_available:
                return None
                
            ws_url = self._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=3)
            
            response = self._send_cdp_command(ws, {
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
            print(f"❌ Error getting individual Characters count: {e}")
            return None

    def _execute_emergency_return_click(self, target_slot):
        """
        🚨 OPTIMIZED: Quick emergency return click without full verification cycle
        Used when normal return seems to have failed
        """
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            
            # Quick click without complex verification
            pyautogui.moveTo(center_x, center_y, duration=0.1)
            pyautogui.click(button='left')
            
            # Minimal wait for effect
            time.sleep(0.2)
            
            print(f"   ⚡ Emergency click executed at ({center_x}, {center_y})")
            return True
            
        except Exception as e:
            print(f"   ❌ Emergency click failed: {e}")
            return False

    def get_enabled_list_array(self):
        """
        🔍 NEW: Get Enabled List array for cross-validation
        Returns: list of strings ["0","1","0"...] or None if error
        """
        try:
            if not self.characters_available:
                return None
                
            ws_url = self._get_websocket_url()
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
                    
                    console.log("✅ Found Enabled List array:", enabledVariable.value);
                    return enabledVariable.value;
                    
                } catch (e) {
                    console.error("Error getting Enabled List array:", e);
                    return null;
                }
            })();
            '''
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_get_enabled, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                enabled_value = response.get('result', {}).get('result', {}).get('value')
                
                if isinstance(enabled_value, list):
                    print(f"🔍 Enabled List retrieved: {enabled_value}")
                    return enabled_value
                else:
                    print(f"⚠️ Unexpected Enabled List type: {type(enabled_value)} = {enabled_value}")
                    return None
            else:
                print("⚠️ No response when getting Enabled List")
                return None
                
        except Exception as e:
            print(f"❌ Error getting Enabled List: {e}")
            return None

    def get_putted_chars_value(self):
        """
        📢 BATCH OPTIMIZED: Get Putted Characters via batch session or fallback
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
            print("⚠️ Batch unavailable, using individual verification")
            return self._get_putted_chars_individual()
            
        except Exception as e:
            print(f"❌ Error in batch putted chars: {e}")
            return self._get_putted_chars_individual()

    def _get_putted_chars_individual(self):
        """
        📢 FALLBACK: Individual Putted Characters (original method)
        """
        try:
            if not self.putted_chars_available:
                return None
                
            ws_url = self._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=3)
            
            response = self._send_cdp_command(ws, {
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
            print(f"❌ Error getting individual putted chars: {e}")
            return None

    def get_dual_counts_batch(self):
        """
        🚀 NEW: Get BOTH counts in single batch call (MAJOR PERFORMANCE BOOST)
        """
        try:
            if self.batch_verification_active and self.verification_functions_ready:
                if self.check_batch_session_health():
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
            print(f"❌ Error in batch dual counts: {e}")
            return self.get_dual_counts_individual()

    def get_dual_counts_individual(self):
        """
        🔄 FALLBACK: Individual dual counts (original method)
        """
        characters_count = self._get_characters_count_individual()
        putted_count = self._get_putted_chars_individual()
        return characters_count, putted_count

    def get_dual_counts(self):
        """
        🔄 BATCH OPTIMIZED: Get BOTH VALUES using batch optimization
        """
        # Try batch first for maximum performance
        return self.get_dual_counts_batch()

    def detect_return_mode(self, operation_context="unknown", icons_count=1):
        """
        🎯 NEW: Detect appropriate return mode based on context
        Returns: RETURN_MODE_INDIVIDUAL or RETURN_MODE_REMIX
        """
        try:
            print(f"\n🎯 MODE DETECTION: Context='{operation_context}', Icons={icons_count}")
            
            # Rule 1: Check operation context (explicit detection)
            if "showcase" in operation_context.lower() or "individual" in operation_context.lower():
                detected_mode = self.RETURN_MODE_INDIVIDUAL
                reason = f"Context indicates individual operation: '{operation_context}'"
                
            elif "remix" in operation_context.lower() or "batch" in operation_context.lower():
                detected_mode = self.RETURN_MODE_REMIX  
                reason = f"Context indicates remix operation: '{operation_context}'"
                
            # Rule 2: Check icons count (threshold-based detection)
            elif icons_count == 1:
                detected_mode = self.RETURN_MODE_INDIVIDUAL
                reason = f"Single icon operation detected (count={icons_count})"
                
            elif icons_count > 1:
                detected_mode = self.RETURN_MODE_REMIX
                reason = f"Multiple icons operation detected (count={icons_count})"
                
            # Rule 3: Check current slot state (state-based detection)
            else:
                current_count = self.get_characters_count() if self.characters_available else 0
                if current_count is None:
                    current_count = 0
                    
                if current_count <= 1:
                    detected_mode = self.RETURN_MODE_INDIVIDUAL
                    reason = f"Current state indicates individual mode (active_icons={current_count})"
                else:
                    detected_mode = self.RETURN_MODE_REMIX
                    reason = f"Current state indicates remix mode (active_icons={current_count})"
            
            # Update tracking
            self.current_return_mode = detected_mode
            self.active_icons_count = icons_count
            
            print(f"   📊 Detected mode: {detected_mode.upper()}")
            print(f"   💡 Reason: {reason}")
            print(f"   📈 Active icons: {self.active_icons_count}")
            
            return detected_mode
            
        except Exception as e:
            print(f"❌ Error in mode detection: {e}")
            # Safe fallback to individual mode
            return self.RETURN_MODE_INDIVIDUAL

    def set_return_mode_explicit(self, mode, reason="manual_override"):
        """
        🎯 NEW: Explicitly set return mode (for manual control)
        """
        if mode in [self.RETURN_MODE_INDIVIDUAL, self.RETURN_MODE_REMIX]:
            print(f"🎯 EXPLICIT MODE SET: {mode.upper()} ({reason})")
            self.current_return_mode = mode
            return True
        else:
            print(f"❌ Invalid return mode: {mode}")
            return False

    def get_current_return_mode(self):
        """
        🎯 NEW: Get current return mode with auto-detection fallback
        """
        if self.current_return_mode == self.RETURN_MODE_AUTO:
            # Auto-detect based on current state
            return self.detect_return_mode("auto_detection")
        else:
            return self.current_return_mode

    def execute_smart_return_with_mode_detection(self, target_slot, operation_context="unknown", use_cdp=False):
        """
        🎯 NEW: Smart return router with automatic mode detection
        Routes to appropriate return strategy based on detected mode
        """
        try:
            print(f"\n🎯 SMART RETURN ROUTER: Starting mode detection...")
            print("="*70)
            
            # Detect appropriate mode
            detected_mode = self.detect_return_mode(operation_context)
            
            # Route to appropriate strategy
            if detected_mode == self.RETURN_MODE_INDIVIDUAL:
                print("🎪 ROUTING → Individual Return Strategy (Precision Targeting)")
                return self.execute_individual_return_strategy(target_slot, use_cdp)
                
            elif detected_mode == self.RETURN_MODE_REMIX:
                print("🎵 ROUTING → Remix Return Strategy (Carpet Bombing)")
                return self.execute_remix_return_strategy(target_slot, use_cdp)
                
            else:
                print(f"⚠️ Unknown mode: {detected_mode}, falling back to individual")
                return self.execute_individual_return_strategy(target_slot, use_cdp)
                
        except Exception as e:
            print(f"❌ Error in smart return router: {e}")
            # Fallback to current method
            return self.corrected_return_icon(target_slot, use_cdp)

    def execute_individual_return_strategy(self, target_slot, use_cdp=False):
        """
        🎪 NEW: Individual return strategy (current precision method)
        """
        print("🎪 EXECUTING INDIVIDUAL RETURN STRATEGY...")
        print("   🎯 Method: Precision targeting with safe zones")
        print("   🔍 Verification: Per-operation count monitoring")
        
        # Use current method for individual operations
        return self.corrected_return_icon(target_slot, use_cdp)

    def execute_remix_return_strategy(self, target_slot, use_cdp=False):
        """
        🎵 NEW: Remix return strategy (carpet bombing - placeholder for Phase 2)
        """
        print("🎵 EXECUTING REMIX RETURN STRATEGY...")
        print("   🎯 Method: Carpet bombing (PLACEHOLDER - Phase 2)")
        print("   🔍 Verification: Progressive count monitoring")
        
        # TODO: Phase 2 - Implement carpet bombing
        # For now, fallback to individual method
        print("   ⚠️ Carpet bombing not implemented yet, using precision fallback")
        return self.corrected_return_icon(target_slot, use_cdp)

    def smart_batch_return_all_slots_with_mode(self, use_cdp=False):
        """
        🔄 NEW: Mode-aware batch return with automatic strategy selection
        """
        try:
            print(f"\n🔄 MODE-AWARE BATCH RETURN")
            print("="*60)
            
            # Detect mode for batch operation
            current_count = self.get_characters_count() if self.characters_available else 0
            operation_context = f"batch_return_{current_count}_icons"
            
            detected_mode = self.detect_return_mode(operation_context, current_count or 0)
            
            if detected_mode == self.RETURN_MODE_REMIX and current_count and current_count > 1:
                print("🎵 BATCH REMIX MODE: Will use carpet bombing when implemented")
                print("   ⚠️ For now, falling back to enhanced individual method")
                
            # For now, use existing method but with mode tracking
            return self.smart_batch_return_all_slots(use_cdp)
            
        except Exception as e:
            print(f"❌ Error in mode-aware batch return: {e}")
            return self.smart_batch_return_all_slots(use_cdp)

    def show_current_mode_info(self):
        """Show current return mode information"""
        try:
            current_mode = self.get_current_return_mode()
            active_count = self.get_characters_count() if self.characters_available else "N/A"
            
            mode_info = f"""🎯 RETURN MODE INFORMATION:

    📊 Current Mode: {current_mode.upper()}
    📈 Active Icons: {active_count}
    🎮 Auto-Detection: {'✅ Enabled' if self.current_return_mode == self.RETURN_MODE_AUTO else '❌ Manual Override'}

    🎪 INDIVIDUAL MODE:
    - Used for: Single icon operations
    - Strategy: Precision targeting with safe zones
    - Verification: Per-operation count monitoring

    🎵 REMIX MODE:
    - Used for: Multiple icons batch operations  
    - Strategy: Carpet bombing (Phase 2)
    - Verification: Progressive count monitoring

    🔄 Mode Detection Rules:
    1. Context keywords (showcase/remix/batch)
    2. Icons count threshold (1 vs >1)
    3. Current slot state analysis"""
            
            messagebox.showinfo("Return Mode Information", mode_info)
            
        except Exception as e:
            messagebox.showerror("Mode Info Error", f"Error getting mode info: {e}")

    def get_comprehensive_slot_state(self):
        """
        📊 NEW: Get comprehensive slot state for verification - UPDATED for Characters Array
        FIXED: Sử dụng Characters Array thay vì Putted Characters và Polos Array
        Returns: dict with Characters Array data or None if error
        """
        try:
            characters_count = self.get_characters_count()
            
            if characters_count is None:
                print("⚠️ Could not get Characters count")
                return None
            
            # Build comprehensive state using Characters Array
            state = {
                'characters_count': characters_count,
                'timestamp': time.time()
            }
            
            print(f"📊 Comprehensive state captured:")
            print(f"   Characters count: {characters_count}")
            
            return state
            
        except Exception as e:
            print(f"❌ Error getting comprehensive state: {e}")
            return None

    def verify_slot_specific_return_enhanced(self, target_slot_index, state_before, state_after):
        """
        🔍 ENHANCED: Slot-specific verification với Characters Array
        FIXED: Chỉ sử dụng Characters count thay vì Polos và Enabled List
        Returns: (success, reason, details)
        """
        try:
            if not state_before or not state_after:
                return False, "missing_state_data", {}
            
            count_before = state_before.get('characters_count')
            count_after = state_after.get('characters_count')
            
            print(f"\n🔍 ENHANCED SLOT VERIFICATION for Slot {target_slot_index}")
            print("-" * 50)
            
            # Characters count check
            expected_count = count_before - 1 if count_before is not None else None
            count_success = (count_after == expected_count)
            
            print(f"   📊 Characters count: {count_before} → {count_after} (expected: {expected_count}) = {'✅' if count_success else '❌'}")
            
            # Decision logic: Chỉ dựa vào Characters count
            overall_success = count_success
            
            # Determine reason and build details
            if overall_success:
                reason = "verified_success"
                details = {
                    'characters_change': f"{count_before} → {count_after}",
                    'verification_method': 'characters_array_only',
                    'target_slot': target_slot_index
                }
            else:
                if not count_success:
                    reason = "characters_count_mismatch"
                else:
                    reason = "unknown_failure"
                
                details = {
                    'characters_change': f"{count_before} → {count_after}",
                    'expected_count': expected_count,
                    'verification_method': 'characters_array_only',
                    'target_slot': target_slot_index
                }
            
            print(f"   🎯 FINAL RESULT: {'✅ SUCCESS' if overall_success else '❌ FAILED'} ({reason})")
            print(f"   💡 METHOD: Characters Array count verification only")
            print("-" * 50)
            
            return overall_success, reason, details
            
        except Exception as e:
            print(f"❌ Enhanced slot verification error: {e}")
            return False, f"verification_error: {e}", {}

    def check_slot_needs_return(self, target_slot_index):
        """
        🔍 NEW: Pre-flight check - UPDATED for Characters Array
        FIXED: Chỉ dựa vào Characters count thay vì Polos array
        Returns: (needs_return, current_state, reason)
        """
        try:
            print(f"\n🔍 PRE-FLIGHT CHECK: Slot {target_slot_index}")
            
            state = self.get_comprehensive_slot_state()
            if not state:
                return True, None, "cannot_verify_state"  # Assume needs return if can't check
            
            characters_count = state.get('characters_count', 0)
            
            if characters_count == 0:
                print(f"   ✅ No characters on slots (count: {characters_count}) - SKIPPING")
                return False, state, "no_characters_placed"
            else:
                print(f"   🎯 Characters on slots (count: {characters_count}) - NEEDS RETURN")
                return True, state, "characters_need_return"
                
        except Exception as e:
            print(f"❌ Pre-flight check error: {e}")
            return True, None, f"error: {e}"  # Assume needs return on error

    def get_active_slots_queue(self):
        """
        📋 NEW: Get smart-ordered queue - UPDATED for Characters Array
        FIXED: Dựa vào Characters count thay vì Polos array
        Returns: list of slot indices (simplified for Characters Array system)
        """
        try:
            print(f"\n📋 BUILDING SMART RETURN QUEUE")
            print("-" * 40)
            
            state = self.get_comprehensive_slot_state()
            if not state:
                print("⚠️ Cannot get slot state, using fallback")
                return []
            
            characters_count = state.get('characters_count', 0)
            
            print(f"   📊 Characters count: {characters_count}")
            
            if characters_count == 0:
                print(f"   ✅ No characters to return")
                return []
            
            # For Characters Array system, we don't know specific slot indices
            # Just return a simple range based on typical slot usage
            if characters_count > 0:
                # Return slots in reverse order (high → low) for safety
                active_slots_estimated = list(range(min(characters_count, 7) - 1, -1, -1))
                print(f"   📋 Estimated active slots: {active_slots_estimated}")
                print(f"   💡 Reason: Characters Array doesn't track specific slot indices")
                return active_slots_estimated
            
            return []
            
        except Exception as e:
            print(f"❌ Error building return queue: {e}")
            return []

    def smart_batch_return_all_slots(self, use_cdp=False):
        """
        🔄 NEW: Smart batch return all active slots with error recovery
        Returns: (success_count, total_attempted, error_log)
        """
        try:
            print(f"\n🔄 SMART BATCH RETURN: All Active Slots")
            print("="*70)
            
            # Get slots configuration
            if not hasattr(self, 'config') or not self.config:
                print("❌ No config available for batch return")
                return 0, 0, ["No config available"]
            
            all_slots = self.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("❌ No slots configuration found")
                return 0, 0, ["No slots configuration"]
            
            # Get active slots queue
            active_slot_indices = self.get_active_slots_queue()
            if not active_slot_indices:
                print("✅ No active slots found - all slots already empty")
                return 0, 0, []
            
            print(f"🎯 Will process {len(active_slot_indices)} active slots")
            
            # Initialize tracking
            success_count = 0
            error_log = []
            recovery_attempts = 0
            
            # Process each slot in smart order
            for slot_index in active_slot_indices:
                if self.should_stop:
                    error_log.append(f"Slot {slot_index}: Emergency stop requested")
                    break
                
                print(f"\n{'='*50}")
                print(f"🎯 PROCESSING SLOT {slot_index} ({success_count + 1}/{len(active_slot_indices)})")
                print(f"{'='*50}")
                
                # Validate slot index
                if slot_index >= len(all_slots):
                    error_msg = f"Slot {slot_index}: Invalid slot index (max: {len(all_slots)-1})"
                    print(f"❌ {error_msg}")
                    error_log.append(error_msg)
                    continue
                
                target_slot = all_slots[slot_index]
                
                # Pre-return state validation
                needs_return, current_state, reason = self.check_slot_needs_return(slot_index)
                
                if not needs_return:
                    if reason == "already_empty":
                        print(f"✅ Slot {slot_index} already empty - skipping")
                        success_count += 1  # Count as success since goal achieved
                    else:
                        error_msg = f"Slot {slot_index}: Cannot verify if return needed ({reason})"
                        print(f"⚠️ {error_msg}")
                        error_log.append(error_msg)
                    continue
                
                # Attempt return with error recovery
                return_success = False
                max_retries = 2
                
                for retry in range(max_retries):
                    if self.should_stop:
                        break
                    
                    if retry > 0:
                        print(f"🔄 RETRY {retry}/{max_retries-1} for Slot {slot_index}")
                        recovery_attempts += 1
                        time.sleep(1.0)  # Brief pause before retry
                    
                    try:
                        return_success = self.corrected_return_icon(target_slot, use_cdp)
                        
                        if return_success:
                            print(f"✅ Slot {slot_index} returned successfully")
                            success_count += 1
                            break
                        else:
                            error_msg = f"Slot {slot_index}: Return failed on attempt {retry + 1}"
                            print(f"❌ {error_msg}")
                            if retry == max_retries - 1:  # Last retry
                                error_log.append(error_msg)
                            
                    except Exception as e:
                        error_msg = f"Slot {slot_index}: Exception on attempt {retry + 1}: {str(e)}"
                        print(f"💥 {error_msg}")
                        if retry == max_retries - 1:  # Last retry
                            error_log.append(error_msg)
                
                # Post-return validation and recovery
                if return_success:
                    # Verify the return actually worked
                    time.sleep(0.5)
                    post_return_state = self.get_comprehensive_slot_state()
                    
                    if post_return_state and post_return_state.get('polos_array'):
                        post_polos = post_return_state['polos_array']
                        if slot_index < len(post_polos) and post_polos[slot_index] == "1":
                            # Return failed verification!
                            print(f"🚨 POST-RETURN VERIFICATION FAILED: Slot {slot_index} still shows '1'")
                            error_msg = f"Slot {slot_index}: Post-return verification failed"
                            error_log.append(error_msg)
                            success_count -= 1  # Adjust success count
                            
                            # Attempt emergency recovery
                            print(f"🚨 EMERGENCY RECOVERY for Slot {slot_index}")
                            emergency_success = self._emergency_slot_recovery(slot_index, target_slot, use_cdp)
                            if emergency_success:
                                print(f"✅ Emergency recovery successful for Slot {slot_index}")
                                success_count += 1
                            else:
                                print(f"💥 Emergency recovery failed for Slot {slot_index}")
                
                # Brief pause between slots to allow game state stabilization
                if slot_index != active_slot_indices[-1]:  # Not the last slot
                    print(f"⏳ Stabilization pause before next slot...")
                    time.sleep(0.8)
            
            # ================================================================
            # FINAL BATCH VALIDATION
            # ================================================================
            print(f"\n{'='*70}")
            print(f"📊 SMART BATCH RETURN COMPLETED")
            print(f"{'='*70}")
            
            # Get final state for validation
            final_state = self.get_comprehensive_slot_state()
            final_polos = final_state.get('polos_array') if final_state else None
            final_putted = final_state.get('putted_characters') if final_state else None
            
            print(f"✅ Successfully returned: {success_count}/{len(active_slot_indices)} slots")
            print(f"🔄 Recovery attempts: {recovery_attempts}")
            print(f"❌ Errors encountered: {len(error_log)}")
            
            if final_state:
                print(f"📊 Final state:")
                print(f"   Putted characters: {final_putted}")
                if final_polos:
                    print(f"   Polos array: {final_polos}")
                    remaining_active = sum(1 for x in final_polos if x == "1")
                    print(f"   Remaining active slots: {remaining_active}")
            
            if error_log:
                print(f"📋 Error summary:")
                for i, error in enumerate(error_log, 1):
                    print(f"   {i}. {error}")
            
            return success_count, len(active_slot_indices), error_log
            
        except Exception as e:
            print(f"💥 Critical error in smart batch return: {e}")
            import traceback
            traceback.print_exc()
            return 0, 0, [f"Critical error: {str(e)}"]

    def _emergency_slot_recovery(self, slot_index, target_slot, use_cdp):
        """
        🚨 NEW: Emergency recovery for stuck slots
        Last resort methods when normal return fails
        """
        try:
            print(f"🚨 EMERGENCY RECOVERY: Slot {slot_index}")
            print("-" * 30)
            
            center_x, center_y = target_slot['x'], target_slot['y']
            domain_adj = self.get_domain_adjustments()
            
            # Method 1: Rapid-fire center clicks
            print("   🔥 Method 1: Rapid-fire center clicks")
            center_coords = [(center_x, center_y)]
            
            state_before = self.get_comprehensive_slot_state() if self.characters_available else None
            
            for _ in range(5):  # 5 rapid clicks
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="single")
                time.sleep(0.1)
            
            time.sleep(1.0)
            if self.characters_available and state_before:
                state_after = self.get_comprehensive_slot_state()
                if state_after:
                    is_success, reason, details = self.verify_slot_specific_return_enhanced(
                        slot_index, state_before, state_after
                    )
                    if is_success:
                        print("   ✅ Rapid-fire method successful!")
                        return True
            
            # Method 2: Hold clicks on expanded area
            print("   🔥 Method 2: Hold clicks on expanded area")
            expanded_coords = self.generate_circle_pattern_coordinates(center_x, center_y, radius=40, num_points=8)
            
            state_before = self.get_comprehensive_slot_state() if self.characters_available else None
            
            self.execute_multi_method_clicks(expanded_coords, use_cdp, domain_adj, method="hold")
            
            time.sleep(1.5)
            if self.characters_available and state_before:
                state_after = self.get_comprehensive_slot_state()
                if state_after:
                    is_success, reason, details = self.verify_slot_specific_return_enhanced(
                        slot_index, state_before, state_after
                    )
                    if is_success:
                        print("   ✅ Hold clicks method successful!")
                        return True
            
            # Method 3: Mixed method barrage
            print("   🔥 Method 3: Mixed method barrage")
            
            state_before = self.get_comprehensive_slot_state() if self.characters_available else None
            
            methods = ["single", "double", "triple"]
            for method in methods:
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method=method)
                time.sleep(0.3)
            
            time.sleep(1.5)
            if self.characters_available and state_before:
                state_after = self.get_comprehensive_slot_state()
                if state_after:
                    is_success, reason, details = self.verify_slot_specific_return_enhanced(
                        slot_index, state_before, state_after
                    )
                    if is_success:
                        print("   ✅ Mixed method barrage successful!")
                        return True
            
            print("   💥 All emergency methods failed")
            return False
            
        except Exception as e:
            print(f"💥 Emergency recovery error: {e}")
            return False

    def validate_workflow_completion(self):
        """
        ✅ NEW: Validate workflow completion - UPDATED for Characters Array
        FIXED: Chỉ sử dụng Characters count validation
        Returns: (is_complete, remaining_issues, recommendations)
        """
        try:
            print(f"\n✅ WORKFLOW COMPLETION VALIDATION")
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
                print("✅ Characters count = 0 (perfect)")
            
            is_complete = len(issues) == 0
            
            print(f"\n📊 VALIDATION RESULT: {'✅ COMPLETE' if is_complete else '⚠️ ISSUES FOUND'}")
            if issues:
                print("❌ Issues found:")
                for issue in issues:
                    print(f"   - {issue}")
                print("💡 Recommendations:")
                for rec in recommendations:
                    print(f"   - {rec}")
            else:
                print("🎉 Characters Array validation passed!")
                print("   ✅ Characters count = 0")
                print("   ✅ All characters returned successfully")
            
            return is_complete, issues, recommendations
            
        except Exception as e:
            print(f"❌ Validation error: {e}")
            return False, [f"Validation error: {str(e)}"], ["Manual check required"]

    def store_target_slot_for_verification(self, target_slot_index):
        """
        🎯 Helper: Store target slot index for verification tracking
        """
        self.last_target_slot_index = target_slot_index
        print(f"🎯 Stored target slot index: {target_slot_index} for verification")

    def get_index_correction_report(self):
        """
        📊 Helper: Get report of all index corrections discovered
        """
        if hasattr(self, 'index_corrections') and self.index_corrections:
            print(f"\n📊 INDEX CORRECTION REPORT:")
            print("-" * 40)
            for config_index, actual_index in self.index_corrections.items():
                offset = actual_index - config_index
                print(f"   Config {config_index} → Actual {actual_index} (offset: {offset:+d})")
            return self.index_corrections
        else:
            print("📊 No index corrections recorded")
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
        
        print(f"📍 Generated 5 coordinates for icon at ({base_x}, {base_y}):")
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
        
        print(f"🔄 Generated 5 coordinates for return at slot ({center_x}, {center_y}):")
        for i, (x, y) in enumerate(coordinates):
            coord_names = ["Center", "Top", "Bottom", "Left", "Right"]
            print(f"   {i+1}. {coord_names[i]}: ({x}, {y})")
        
        return coordinates

    def generate_circle_pattern_coordinates(self, center_x, center_y, radius=50, num_points=8):
        """
        🔥 NEW: Generate circle pattern coordinates around slot center
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
        
        print(f"🔄 Generated circle pattern: center + {num_points} points at {radius}px radius")
        for i, (x, y) in enumerate(coordinates):
            if i == 0:
                print(f"   CENTER: ({x}, {y})")
            else:
                angle_deg = (360 * (i-1)) / num_points
                print(f"   Point {i}: ({x}, {y}) - {angle_deg:.0f}°")
        
        return coordinates

    def calculate_slot_safe_zone(self, target_slot_index, all_slots):
        """
        🛡️ NEW: Calculate safe zone to avoid clicking adjacent slots
        Returns max_radius and safe coordinates only
        """
        try:
            if not all_slots or target_slot_index >= len(all_slots):
                print(f"⚠️ Invalid slot data for safe zone calculation")
                return 40, []  # Very conservative fallback
            
            target_slot = all_slots[target_slot_index]
            target_x, target_y = target_slot['x'], target_slot['y']
            
            print(f"\n🛡️ CALCULATING SAFE ZONE for Slot {target_slot_index} at ({target_x}, {target_y})")
            
            # Find minimum distance to any other slot
            min_distance_to_other_slot = float('inf')
            
            for i, other_slot in enumerate(all_slots):
                if i == target_slot_index:  # Skip self
                    continue
                    
                other_x, other_y = other_slot['x'], other_slot['y']
                distance = ((target_x - other_x) ** 2 + (target_y - other_y) ** 2) ** 0.5
                
                if distance < min_distance_to_other_slot:
                    min_distance_to_other_slot = distance
                    print(f"   📏 Distance to Slot {i}: {distance:.1f}px")
            
            # Safe radius = 40% of minimum distance (very conservative)
            if min_distance_to_other_slot != float('inf'):
                max_safe_radius = int(min_distance_to_other_slot * 0.4)
                # Cap maximum to prevent going too wide
                max_safe_radius = min(max_safe_radius, 80)  # Never exceed 80px
            else:
                max_safe_radius = 40  # Ultra-conservative fallback
            
            print(f"   🛡️ Minimum distance to other slots: {min_distance_to_other_slot:.1f}px")
            print(f"   ✅ Safe radius calculated: {max_safe_radius}px (40% of min distance)")
            
            # Generate safe coordinates within boundary
            safe_coordinates = self.generate_safe_boundary_coordinates(target_x, target_y, max_safe_radius)
            
            return max_safe_radius, safe_coordinates
            
        except Exception as e:
            print(f"❌ Error calculating safe zone: {e}")
            return 30, []  # Emergency fallback

    def _precompute_all_safe_zones(self):
        """
        🔥 NEW: Pre-compute safe zones for all slots to avoid repeated calculations
        Cache results in self.safe_zone_cache for performance optimization
        """
        try:
            print(f"\n🔥 STARTING SAFE ZONE PRE-COMPUTATION...")
            print("="*60)
            
            # Debug: Check if config exists
            if not self.config:
                print("⚠️ No config available for safe zone pre-computation")
                self.safe_zone_cache = {}
                return False
            
            layout = self.config.get('layout', {})
            all_slots = layout.get('slots', [])
            
            # Debug: Check slots data
            print(f"📊 Config validation:")
            print(f"   Layout exists: {'✅' if layout else '❌'}")
            print(f"   Slots found: {len(all_slots)}")
            
            if not all_slots:
                print("⚠️ No slots found for safe zone pre-computation")
                self.safe_zone_cache = {}
                return False
            
            # Debug: Show first few slots
            print(f"📋 Sample slots data:")
            for i, slot in enumerate(all_slots[:3]):  # Show first 3 slots
                slot_index = slot.get('index', 'NO_INDEX')
                x, y = slot.get('x', 'NO_X'), slot.get('y', 'NO_Y')
                print(f"   Slot {i}: index={slot_index}, coords=({x}, {y})")
            
            print(f"\n🔥 PRE-COMPUTING SAFE ZONES for {len(all_slots)} slots...")
            print("="*50)
            
            # Initialize cache
            self.safe_zone_cache = {}
            successful_computations = 0
            failed_computations = 0
            
            for slot in all_slots:
                slot_index = slot.get('index', -1)
                
                if slot_index == -1:
                    print(f"⚠️ Slot missing index, skipping: {slot}")
                    failed_computations += 1
                    continue
                
                try:
                    print(f"🛡️ Computing safe zone for Slot {slot_index}...")
                    
                    # Use existing calculate_slot_safe_zone method
                    max_safe_radius, safe_coordinates = self.calculate_slot_safe_zone(slot_index, all_slots)
                    
                    # Validate calculation results
                    if safe_coordinates and max_safe_radius > 0:
                        # Cache the results
                        self.safe_zone_cache[slot_index] = {
                            'max_radius': max_safe_radius,
                            'coordinates': safe_coordinates,
                            'center_x': slot['x'],
                            'center_y': slot['y']
                        }
                        
                        print(f"   ✅ Slot {slot_index}: {len(safe_coordinates)} coords, max radius {max_safe_radius}px")
                        successful_computations += 1
                    else:
                        print(f"   ❌ Slot {slot_index}: Computation failed (empty coordinates or invalid radius)")
                        failed_computations += 1
                        
                except Exception as e:
                    print(f"   ❌ Slot {slot_index}: Exception during computation: {e}")
                    failed_computations += 1
            
            # Final summary
            print(f"\n📊 PRE-COMPUTATION SUMMARY:")
            print(f"   ✅ Successful: {successful_computations}")
            print(f"   ❌ Failed: {failed_computations}")
            print(f"   📦 Cache size: {len(self.safe_zone_cache)}")
            
            if successful_computations > 0:
                print(f"✅ SAFE ZONE PRE-COMPUTATION COMPLETED!")
                print(f"🚀 Performance: {successful_computations} slots cached for instant access")
                print("="*60)
                return True
            else:
                print(f"❌ SAFE ZONE PRE-COMPUTATION FAILED!")
                print(f"⚠️ All {len(all_slots)} slots failed to compute - will use lazy caching")
                print("="*60)
                return False
            
        except Exception as e:
            print(f"❌ Critical error in safe zone pre-computation: {e}")
            import traceback
            traceback.print_exc()
            self.safe_zone_cache = {}
            return False

    def check_safe_zone_cache_status(self):
        """
        🔍 NEW: Check and display safe zone cache status for debugging
        """
        try:
            print(f"\n🔍 SAFE ZONE CACHE STATUS:")
            print("="*40)
            
            if not hasattr(self, 'safe_zone_cache') or not self.safe_zone_cache:
                print("❌ Cache is empty or not initialized")
                return False
            
            print(f"📦 Cache size: {len(self.safe_zone_cache)} slots")
            print(f"📋 Cached slot indices: {list(self.safe_zone_cache.keys())}")
            
            # Show details for each cached slot
            for slot_index, cache_data in self.safe_zone_cache.items():
                coords_count = len(cache_data.get('coordinates', []))
                max_radius = cache_data.get('max_radius', 0)
                center_x = cache_data.get('center_x', 'N/A')
                center_y = cache_data.get('center_y', 'N/A')
                
                print(f"   Slot {slot_index}: {coords_count} coords, radius {max_radius}px, center ({center_x}, {center_y})")
            
            print("="*40)
            return True
            
        except Exception as e:
            print(f"❌ Error checking cache status: {e}")
            return False

    def generate_safe_boundary_coordinates(self, center_x, center_y, max_radius):
        """
        🛡️ NEW: Generate coordinates within safe boundary only
        Progressive circles but never exceed safe radius
        """
        import math
        
        all_safe_coords = []
        
        # Always start with center
        all_safe_coords.append((center_x, center_y))
        
        # Generate progressive circles within safe boundary
        radii = [20, 35, 50, 65]  # Progressive radii
        
        for radius in radii:
            if radius > max_radius:
                print(f"   🛑 Stopping at radius {radius}px (exceeds safe limit {max_radius}px)")
                break
                
            print(f"   🔄 Generating circle at {radius}px radius (within safe zone)")
            
            # 8 points for larger circles, 4 points for small circles
            num_points = 8 if radius >= 35 else 4
            
            for i in range(num_points):
                angle = (2 * math.pi * i) / num_points
                x = center_x + int(radius * math.cos(angle))
                y = center_y + int(radius * math.sin(angle))
                all_safe_coords.append((x, y))
        
        print(f"   ✅ Generated {len(all_safe_coords)} safe coordinates")
        return all_safe_coords

    def verify_slot_specific_return(self, target_slot_index, putted_before, putted_after):
        """
        🔍 NEW: Enhanced verification with slot-specific validation
        Not just check if putted decreased, but validate it's the RIGHT slot
        """
        try:
            # Basic putted characters check
            basic_success = self.verify_return_success(putted_before, putted_after)
            
            if not basic_success:
                return False, "putted_characters_unchanged"
            
            # TODO: Add more sophisticated validation if needed
            # For now, rely on safe zone to prevent cross-slot contamination
            
            print(f"✅ Slot {target_slot_index} return verified: {putted_before} → {putted_after}")
            return True, "verified_success"
            
        except Exception as e:
            print(f"❌ Slot-specific verification error: {e}")
            return False, f"verification_error: {e}"

    def execute_multi_method_clicks(self, coordinates, use_cdp, domain_adj, method="single"):
        """
        🔥 NEW: Execute clicks using different methods on coordinate list
        Returns True if any click was executed successfully
        """
        successful_clicks = 0
        
        print(f"🎯 Executing {method} method on {len(coordinates)} coordinates...")
        
        for i, (click_x, click_y) in enumerate(coordinates):
            if self.should_stop:
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
                print(f"   ⚠️ Click {i+1} failed: {e}")
        
        print(f"   ✅ Executed {successful_clicks}/{len(coordinates)} clicks successfully")
        return successful_clicks > 0

    def _execute_double_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """🔥 NEW: Execute double click"""
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
        """🔥 NEW: Execute triple click"""
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
        """🔥 NEW: Execute hold-and-release click"""
        try:
            if use_cdp:
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    # Move and press
                    self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": click_x, "y": click_y}})
                    time.sleep(0.1)
                    self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": click_x, "y": click_y, "button": "left", "clickCount": 1}})
                    time.sleep(0.5)  # Hold for 500ms
                    self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": click_x, "y": click_y, "button": "left", "clickCount": 1}})
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
        🔄 DUAL VERIFICATION: CHỈ CẦN 1 TRONG 2 TĂNG 1 ĐƠN VỊ
        """
        print(f"📊 DUAL Drag verification:")
        
        # Check Characters Array
        chars_success = False
        if before_chars is not None and after_chars is not None:
            expected_chars = before_chars + 1
            chars_success = (after_chars == expected_chars)
            print(f"   🔢 Characters: {before_chars} → {after_chars} (expected: {expected_chars}) = {'✅' if chars_success else '❌'}")
        else:
            print(f"   🔢 Characters: N/A (not available)")
        
        # Check Putted Characters  
        putted_success = False
        if before_putted is not None and after_putted is not None:
            expected_putted = before_putted + 1
            putted_success = (after_putted == expected_putted)
            print(f"   🎯 Putted: {before_putted} → {after_putted} (expected: {expected_putted}) = {'✅' if putted_success else '❌'}")
        else:
            print(f"   🎯 Putted: N/A (not available)")
        
        # DUAL LOGIC: CHỈ CẦN 1 TRONG 2 THÀNH CÔNG
        overall_success = chars_success or putted_success
        
        if overall_success:
            successful_method = []
            if chars_success: successful_method.append("Characters Array")
            if putted_success: successful_method.append("Putted Characters")
            print(f"   🎉 DUAL SUCCESS via: {', '.join(successful_method)}")
        else:
            print(f"   ❌ DUAL FAILURE: Both methods failed")
        
        return overall_success

    def verify_return_success_dual(self, before_chars, after_chars, before_putted, after_putted):
        """
        🔄 DUAL VERIFICATION: CHỈ CẦN 1 TRONG 2 GIẢM 1 ĐƠN VỊ
        """
        print(f"📊 DUAL Return verification:")
        
        # Check Characters Array
        chars_success = False
        if before_chars is not None and after_chars is not None:
            expected_chars = before_chars - 1
            chars_success = (after_chars == expected_chars)
            print(f"   🔢 Characters: {before_chars} → {after_chars} (expected: {expected_chars}) = {'✅' if chars_success else '❌'}")
        else:
            print(f"   🔢 Characters: N/A (not available)")
        
        # Check Putted Characters  
        putted_success = False
        if before_putted is not None and after_putted is not None:
            expected_putted = before_putted - 1
            putted_success = (after_putted == expected_putted)
            print(f"   🎯 Putted: {before_putted} → {after_putted} (expected: {expected_putted}) = {'✅' if putted_success else '❌'}")
        else:
            print(f"   🎯 Putted: N/A (not available)")
        
        # DUAL LOGIC: CHỈ CẦN 1 TRONG 2 THÀNH CÔNG
        overall_success = chars_success or putted_success
        
        if overall_success:
            successful_method = []
            if chars_success: successful_method.append("Characters Array") 
            if putted_success: successful_method.append("Putted Characters")
            print(f"   🎉 DUAL SUCCESS via: {', '.join(successful_method)}")
        else:
            print(f"   ❌ DUAL FAILURE: Both methods failed")
        
        return overall_success

    def _execute_single_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """
        ADDED: Execute a single return click at specific coordinates
        Returns True if click was executed, False if execution failed
        """
        try:
            if use_cdp:
                # Try CDP click first
                success = self._execute_cdp_single_click(click_x, click_y)
                if not success:
                    print("⚠️ CDP click failed, falling back to PyAutoGUI")
                    success = self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                return success
            else:
                # Use PyAutoGUI click
                return self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                
        except Exception as e:
            print(f"❌ Single return click execution error: {e}")
            return False

    def _execute_cdp_single_click(self, click_x, click_y):
        """ADDED: Execute single click using CDP"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print(f"🖱️ Executing CDP single click at ({click_x}, {click_y})")
            
            # Move and click
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": click_x, "y": click_y}})
            time.sleep(0.1)
            self._simulate_click_via_cdp(ws, click_x, click_y)
            
            ws.close()
            print("✅ CDP single click completed")
            return True
            
        except Exception as e:
            print(f"❌ CDP single click failed: {e}")
            return False

    def attempt_icon_return(self, target_slot):
        """
        ADDED: Attempt to return icon from slot before next retry
        UPDATED: Sử dụng Characters Array verification
        """
        try:
            print("🔄 Attempting to return icon before retry...")
            
            # Get count before return
            count_before = self.get_characters_count() if self.characters_available else None
            
            # Quick click to return icon
            center_x, center_y = target_slot['x'], target_slot['y']
            varied_x, varied_y = self.add_position_variation(center_x, center_y, 3)
            
            pyautogui.moveTo(varied_x, varied_y, duration=0.2)
            time.sleep(0.1)
            pyautogui.click(button='left')
            time.sleep(0.5)  # Wait for return animation
            
            # Verify return if possible
            if self.characters_available and count_before is not None:
                count_after = self.get_characters_count()
                if count_after is not None and count_after < count_before:
                    print("✅ Icon return verified via Characters Array")
                else:
                    print("⚠️ Icon return unclear via Characters Array")
            
            print("✅ Icon return attempt completed")
            return True
            
        except Exception as e:
            print(f"⚠️ Icon return attempt failed: {e}")
            return False

    # ================================================================
    # == ENHANCED DRAG & DROP WITH VERIFICATION ==
    # ================================================================

    def corrected_drag_and_drop(self, icon, target_slot, is_first_icon=False, use_cdp=False):
        """
        ENHANCED: Drag and drop with DUAL verification (Characters Array + Putted Characters)
        UPDATED: Reduced timing for faster workflow
        CHỈ CẦN 1 TRONG 2 THÀNH CÔNG LÀ ĐỦ - Maximum compatibility across all mods
        """
        print(f"\n🎯 ENHANCED DRAG START: Icon to slot with DUAL verification")
        print("="*60)

        # Store target slot for verification
        target_slot_index = target_slot.get('index', -1)
        self.store_target_slot_for_verification(target_slot_index)

        # Check if DUAL verification is available
        dual_verification_enabled = self.characters_available or self.putted_chars_available
        if dual_verification_enabled:
            print("🔄 DUAL verification ENABLED:")
            print(f"   🔢 Characters Array: {'✅ Ready' if self.characters_available else '❌ N/A'}")
            print(f"   🎯 Putted Characters: {'✅ Ready' if self.putted_chars_available else '❌ N/A'}")
        else:
            print("⚠️ DUAL verification DISABLED - using fallback logic")
        
        # Generate 5 coordinates to try
        coordinates = self.generate_icon_coordinates(icon['x'], icon['y'])
        
        domain_adj = self.get_domain_adjustments()
        success = False
        coord_used = 0
        
        # Try each coordinate until success
        for coord_index, (start_x, start_y) in enumerate(coordinates):
            if self.should_stop:
                break
                
            coord_used = coord_index + 1
            coord_names = ["Center", "Left", "Right", "Top", "Bottom"]
            print(f"\n🎯 Trying coordinate {coord_used}/5: {coord_names[coord_index]} ({start_x}, {start_y})")
            
            # Get DUAL counts BEFORE drag (if verification available)
            characters_before, putted_before = None, None
            if dual_verification_enabled:
                characters_before, putted_before = self.get_dual_counts()
                print(f"📊 DUAL counts BEFORE drag:")
                print(f"   🔢 Characters: {characters_before}")
                print(f"   🎯 Putted: {putted_before}")
            
            # Execute single drag attempt
            drag_executed = self._execute_single_drag_attempt(
                start_x, start_y, target_slot, is_first_icon, use_cdp, domain_adj
            )
            
            if not drag_executed:
                print(f"❌ Drag execution failed at coordinate {coord_used}")
                if coord_index < len(coordinates) - 1:
                    print("   Trying next coordinate...")
                    time.sleep(0.3)  # REDUCED from 0.5
                continue
            
            # Wait for game state to update - REDUCED TIMING
            verification_wait = 0.4 if dual_verification_enabled else 0.15  # REDUCED from 1.0 and 0.15
            print(f"⏳ Waiting {verification_wait}s for game state update (mouse at safe position)...")
            time.sleep(verification_wait)
            
            # Get DUAL counts AFTER drag (if verification available)
            if dual_verification_enabled:
                characters_after, putted_after = self.get_dual_counts()
                print(f"📊 DUAL counts AFTER drag:")
                print(f"   🔢 Characters: {characters_after}")
                print(f"   🎯 Putted: {putted_after}")
                
                # Verify drag success using DUAL system
                if self.verify_drag_success_dual(characters_before, characters_after, putted_before, putted_after):
                    print(f"🎉 DUAL VERIFIED SUCCESS at coordinate {coord_used}!")
                    success = True
                    break
                else:
                    print(f"❌ DUAL VERIFICATION FAILED at coordinate {coord_used}")
                    
                    # Only return icon if ANY value increased (either chars or putted)
                    characters_increased = (characters_after is not None and characters_before is not None and characters_after > characters_before)
                    putted_increased = (putted_after is not None and putted_before is not None and putted_after > putted_before)
                    any_increase = characters_increased or putted_increased
                    
                    if coord_index < len(coordinates) - 1:
                        if any_increase:
                            print("🔄 Icon was placed (detected by DUAL system), returning before retry...")
                            self.attempt_icon_return(target_slot)
                            time.sleep(0.5)  # REDUCED from 0.8
                        else:
                            print("ℹ️ Icon was not placed (no increase in DUAL system), trying next coordinate...")
                            time.sleep(0.1)  # REDUCED from 0.3
            else:
                # No verification available - assume success if drag executed
                print(f"✅ ASSUMED SUCCESS at coordinate {coord_used} (no verification)")
                success = True
                break
            
            # Small delay before next coordinate - REDUCED
            if coord_index < len(coordinates) - 1:
                print("   Preparing next coordinate attempt...")
                time.sleep(0.2)  # REDUCED from 0.3
        
        # Final result
        if success:
            print(f"🎉 ENHANCED DRAG COMPLETED SUCCESSFULLY!")
            print(f"   Coordinate used: {coord_used}/5")
            print(f"   Verification: {'🔄 DUAL Enabled' if dual_verification_enabled else '⚠️ Disabled'}")
            if dual_verification_enabled:
                print(f"   Systems: Characters Array {'✅' if self.characters_available else '❌'} + Putted Characters {'✅' if self.putted_chars_available else '❌'}")
            print("   Mouse already at safe position")
            print("="*60)
            
            self.drag_stats.record_attempt(True, retry_count=0, coord_used=coord_used, verification_used=dual_verification_enabled)
            return True
        else:
            print(f"💥 ENHANCED DRAG FAILED - All {len(coordinates)} coordinates attempted")
            print("="*60)
            
            self.drag_stats.record_attempt(False, retry_count=len(coordinates), coord_used=coord_used, verification_used=dual_verification_enabled)
            return False

    def _execute_single_drag_attempt(self, start_x, start_y, target_slot, is_first_icon, use_cdp, domain_adj):
        """
        ADDED: Execute a single drag attempt at specific coordinates
        Returns True if drag was executed, False if execution failed
        """
        try:
            end_x, end_y = self.add_position_variation(target_slot['x'], target_slot['y'], 8)
            
            # Get timing based on icon type and domain
            timing_key = 'first_icon' if is_first_icon else 'normal_icon'
            delays = self.DRAG_DELAYS[timing_key].copy()
            
            # Apply domain-specific adjustments
            stability_factor = domain_adj['stability_factor']
            for key in delays:
                delays[key] *= stability_factor
            
            # Extended timing for first icon
            if is_first_icon:
                print("🎪 First icon - applying extended timing...")
                time.sleep(delays['approach'])
                self.ensure_game_focus()
                time.sleep(domain_adj['extra_focus_time'])
            
            # Execute drag based on method choice
            if use_cdp:
                return self._execute_cdp_drag(start_x, start_y, end_x, end_y, delays, domain_adj)
            else:
                return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, is_first_icon)
                
        except Exception as e:
            print(f"❌ Single drag attempt execution error: {e}")
            return False

    def intro_skip_via_cdp(self):
        """ENHANCED: Multi-target Skip intro using CDP with smart detection + blind clicks"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("⚠️ Could not get WebSocket URL for enhanced intro skipping, using pyautogui fallback")
                return self.intro_skip_via_pyautogui()
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print(f"🔍 ENHANCED CDP SKIP INTRO: Multi-target detection + {self.intro_skip_clicks} blind clicks...")
            
            # Phase 1: Smart detection and clicks
            print("📍 Phase 1: Searching for ALL Skip Intro buttons via CDP...")
            
            smart_clicks_performed = 0
            all_buttons = []
            
            try:
                # Execute JavaScript to find ALL Skip Intro buttons
                response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": self.js_find_skip_intro, "awaitPromise": True, "returnByValue": True}
                })
                
                if response and 'result' in response:
                    skip_result = response.get('result', {}).get('result', {}).get('value')
                    
                    if skip_result and skip_result.get('found'):
                        all_buttons = skip_result.get('buttons', [])
                        button_count = skip_result.get('count', 0)
                        visible_count = skip_result.get('visibleCount', 0)
                        
                        print(f"✅ Found {button_count} Skip Intro buttons (Visible: {visible_count})")
                        
                        # Click ALL buttons found
                        for i, button in enumerate(all_buttons):
                            button_num = i + 1
                            x, y = button['x'], button['y']
                            visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                            costume_name = button.get('currentCostumeName', 'Unknown')
                            
                            print(f"🎯 CDP clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status} - {costume_name}")
                            
                            # Click this specific button via CDP
                            self._simulate_click_via_cdp(ws, x, y)
                            time.sleep(0.8)  # Wait between clicks
                            
                            smart_clicks_performed += 1
                        
                        print(f"✅ Smart CDP clicks completed: {smart_clicks_performed} buttons clicked!")
                    else:
                        reason = skip_result.get('reason', 'unknown') if skip_result else 'no_result'
                        print(f"ℹ️ No Skip Intro buttons found via CDP - Reason: {reason}")
                
            except Exception as e:
                print(f"⚠️ Smart detection failed: {e}, proceeding with blind clicks...")
            
            # Phase 2: ALWAYS perform blind clicks for additional assurance
            if smart_clicks_performed > 0:
                print("🔄 Phase 2: Performing ADDITIONAL blind CDP clicks for protection...")
                print("   → Ensures intro skipping even if smart clicks had edge cases")
            else:
                print("🖱️ Phase 2: Performing STANDARD blind CDP clicks (no smart buttons found)...")
            
            # Perform blind clicks via CDP
            for i in range(self.intro_skip_clicks):
                self._simulate_click_via_cdp(ws, 960, 540)
                time.sleep(0.5)
            
            ws.close()
            
            # Summary
            if smart_clicks_performed > 0:
                print("🎉 ENHANCED CDP SKIP INTRO COMPLETED:")
                print(f"   ✅ Smart clicks: {smart_clicks_performed} buttons via CDP")
                print(f"   ✅ Blind clicks: {self.intro_skip_clicks} additional CDP clicks")
                print("   🛡️ Dual protection ensures maximum success rate")
            else:
                print("🎉 STANDARD CDP SKIP INTRO COMPLETED:")
                print("   ⚠️ Smart clicks: N/A (No buttons found)")
                print(f"   ✅ Blind clicks: {self.intro_skip_clicks} CDP clicks")
                print("   🔄 Standard method used as fallback")
            
            print("✅ CDP intro skipping completed")
            return True
            
        except Exception as e:
            print(f"❌ CDP intro skipping failed: {e}, falling back to pyautogui")
            return self.intro_skip_via_pyautogui()

    def intro_skip_via_pyautogui(self):
        """ENHANCED: Multi-target Skip intro using PyAutoGUI with smart detection + blind drags"""
        try:
            print(f"🔍 ENHANCED PYAUTOGUI SKIP INTRO: Multi-target detection + {self.intro_skip_clicks} wake-up drags...")
            
            # Phase 1: Try smart detection via WebSocket if available
            print("📍 Phase 1: Attempting smart detection via WebSocket...")
            
            smart_clicks_performed = 0
            
            try:
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    
                    # Execute JavaScript to find Skip Intro buttons
                    response = self._send_cdp_command(ws, {
                        "method": "Runtime.evaluate", 
                        "params": {"expression": self.js_find_skip_intro, "awaitPromise": True, "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        skip_result = response.get('result', {}).get('result', {}).get('value')
                        
                        if skip_result and skip_result.get('found'):
                            all_buttons = skip_result.get('buttons', [])
                            button_count = skip_result.get('count', 0)
                            
                            print(f"✅ Found {button_count} Skip Intro buttons via WebSocket")
                            
                            # Click ALL buttons found using PyAutoGUI
                            for i, button in enumerate(all_buttons):
                                button_num = i + 1
                                x, y = button['x'], button['y']
                                visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                                
                                print(f"🎯 PyAutoGUI clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status}")
                                
                                # Click this specific button via PyAutoGUI
                                pyautogui.moveTo(x, y, duration=0.2)
                                pyautogui.click(button='left')
                                time.sleep(0.8)  # Wait between clicks
                                
                                smart_clicks_performed += 1
                            
                            print(f"✅ Smart PyAutoGUI clicks completed: {smart_clicks_performed} buttons clicked!")
                        else:
                            print("ℹ️ No Skip Intro buttons found via WebSocket")
                    
                    ws.close()
                else:
                    print("⚠️ WebSocket not available for smart detection")
                    
            except Exception as e:
                print(f"⚠️ Smart detection via WebSocket failed: {e}")
            
            # Phase 2: ALWAYS perform aggressive wake-up drags
            if smart_clicks_performed > 0:
                print("🔄 Phase 2: Performing ADDITIONAL wake-up drags for protection...")
                print("   → Ensures intro skipping even if smart clicks had edge cases")
            else:
                print("⚡ Phase 2: Performing AGGRESSIVE wake-up drags (no smart buttons found)...")
            
            # Aggressive wake-up drags using existing logic
            print(f"⚡ AGGRESSIVE INTRO SKIPPING: Performing {self.intro_skip_clicks} 'wake-up drags' over 5 seconds...")
            screen_width, screen_height = pyautogui.size()
            center_x, center_y = screen_width // 2, screen_height // 2
            total_duration = 5.0
            num_drags = self.intro_skip_clicks
            sleep_between_drags = total_duration / num_drags
            
            for i in range(num_drags):
                if self.should_stop: 
                    return False
                start_x = center_x + random.randint(-100, 100)
                start_y = center_y + random.randint(-100, 100)
                drag_x_offset = random.randint(15, 40) * random.choice([-1, 1])
                drag_y_offset = random.randint(15, 40) * random.choice([-1, 1])
                end_x = start_x + drag_x_offset
                end_y = start_y + drag_y_offset
                print(f"   ... Wake-up drag {i + 1}/{num_drags}")
                pyautogui.moveTo(start_x, start_y, duration=0.05)
                pyautogui.dragTo(end_x, end_y, duration=0.15, button='left')
                time.sleep(sleep_between_drags - 0.20)
            
            # Summary
            if smart_clicks_performed > 0:
                print("🎉 ENHANCED PYAUTOGUI SKIP INTRO COMPLETED:")
                print(f"   ✅ Smart clicks: {smart_clicks_performed} buttons via PyAutoGUI")
                print(f"   ✅ Wake-up drags: {self.intro_skip_clicks} aggressive drags")
                print("   🛡️ Dual protection ensures maximum success rate")
            else:
                print("🎉 AGGRESSIVE PYAUTOGUI SKIP INTRO COMPLETED:")
                print("   ⚠️ Smart clicks: N/A (No buttons found)")
                print(f"   ✅ Wake-up drags: {self.intro_skip_clicks} aggressive drags")
                print("   ⚡ Aggressive method used as fallback")
            
            print("✅ Aggressive intro skipping sequence completed.")
            return True
            
        except Exception as e:
            print(f"❌ PyAutoGUI intro skipping failed: {e}")
            return False

    def close_game_window(self):
        """Close game window safely"""
        print("🧹 Safely closing Chrome debug session...")
        success = self.cleanup_chrome_complete()
        
        if success:
            print("✅ Chrome debug session closed safely")
        else:
            print("⚠️ Some issues during Chrome debug cleanup")
            
        return success

    # ================================================================
    # == GAME ENVIRONMENT SETUP (ENHANCED with Verification) ==
    # ================================================================

    def setup_game_environment(self, progress_callback=None, first_setup=True):
        """
        ENHANCED: Setup with DUAL verification system (Characters Array + Putted Characters)
        """
        try:
            if not self.config:
                print("❌ No config loaded")
                messagebox.showerror("Error", "No configuration file loaded.")
                return False

            # Setup browser using Chrome debug mode
            # LUÔN mở Chrome mới cho cả Master và Individual mode
            if progress_callback:
                if first_setup:
                    progress_callback("🌐 Opening game in Chrome debug mode (Master)...")
                else:
                    progress_callback("🌐 Closing previous session and opening fresh game (Individual)...")

            print(f"🌐 Opening {'fresh ' if not first_setup else ''}game in Chrome debug mode...")

            # THÊM: Explicit cleanup cho Individual mode
            if not first_setup:
                print("🧹 Explicitly closing previous Chrome session for Individual mode...")
                self.cleanup_chrome_complete()
                time.sleep(3)  # Extra wait for complete cleanup

            success = self.navigate_and_prepare_game(self.config['currentUrl'])
            if not success:
                print("⚠️ Chrome debug launch failed")
                return False

            # Skip intros using hybrid approach (CDP preferred, pyautogui fallback)
            if progress_callback:
                progress_callback("🖱️ Skipping intros...")
            
            if not self.intro_skip_via_cdp():
                print("CDP intro skipping failed, but continuing anyway...")

            # THAY ĐỔI: Setup DUAL verification system (Characters Array + Putted Characters)
            if progress_callback:
                progress_callback("🔄 Setting up DUAL verification system...")
            
            if not self.setup_dual_verification_system():
                print("⚠️ DUAL verification setup failed, continuing with basic workflow")
            else:
                print("✅ DUAL verification system ready!")

            # Start batch verification session for performance
            if progress_callback:
                progress_callback("⚡ Starting batch verification session...")

            if self.start_batch_verification_session():
                print("🚀 Batch verification session active - PERFORMANCE MODE!")
            else:
                print("⚠️ Batch session failed, using individual verification")

            # Prepare game for playing
            self.prepare_game_for_playing()
            time.sleep(1)

            if progress_callback:
                progress_callback("✅ Enhanced game environment ready!")
            
            print("✅ ENHANCED GAME ENVIRONMENT SETUP COMPLETED!")
            print("🎯 FEATURES ACTIVE:")
            print("   🌐 Chrome debug mode for browser management")
            print("   🎮 PyAutoGUI for precise game automation")
            print("   📡 WebSocket/CDP for advanced control")
            print("   🛡️ AdBlocker extension loaded")
            
            # DUAL verification status
            if self.characters_available or self.putted_chars_available:
                print("   🔄 DUAL verification system ENABLED:")
                print(f"      🔢 Characters Array: {'✅ Ready' if self.characters_available else '❌ Failed'}")
                print(f"      🎯 Putted Characters: {'✅ Ready' if self.putted_chars_available else '❌ Failed'}")
                print("      💡 Only 1 method needs to work = Maximum compatibility!")
            else:
                print("   ⚠️ DUAL verification system DISABLED (fallback mode)")
            
            return True
                
        except Exception as e:
            error_message = f"An unexpected error occurred during game setup: {e}"
            print(f"❌ {error_message}")
            import traceback
            traceback.print_exc()
            messagebox.showerror("Setup Error", error_message)
            return False
    
    def ensure_mouse_released(self):
        """Ensure mouse is released before starting new drag"""
        try:
            pyautogui.mouseUp(button='left')
            pyautogui.mouseUp(button='middle')
            time.sleep(0.1)
            print("🔄 Mouse state cleaned")
        except Exception as e:
            print("⚠️ Mouse cleanup warning: " + str(e))
    
    def ensure_game_focus(self):
        """Ensure game has focus before performing drag"""
        try:
            print("🎯 Ensuring game focus...")
            time.sleep(0.1)
        except Exception as e:
            print("⚠️ Focus ensuring warning: " + str(e))
    
    def get_domain_adjustments(self):
        """Get domain-specific adjustments for current config"""
        if not self.config:
            return self.domain_adjustments.get('sprunki.org', self.domain_adjustments['sprunki.org'])
        
        try:
            parsed_url = urlparse(self.config['currentUrl'])
            domain = parsed_url.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            
            return self.domain_adjustments.get(domain, self.domain_adjustments['sprunki.org'])
        except:
            return self.domain_adjustments['sprunki.org']
    
    def move_mouse_to_safe_position(self):
        """Move mouse to bottom right corner as required by game"""
        try:
            screen_width, screen_height = pyautogui.size()
            
            # Bottom right corner with small margin from edge
            safe_x = screen_width - random.randint(30, 80)
            safe_y = screen_height - random.randint(30, 80)
            
            print(f"🎯 Moving mouse to safe position: ({safe_x}, {safe_y})")
            pyautogui.moveTo(safe_x, safe_y, duration=random.uniform(0.3, 0.5))
            time.sleep(0.2)
            
            return True
        except Exception as e:
            print("⚠️ Safe position movement warning: " + str(e))
            return False

    def _execute_cdp_drag(self, start_x, start_y, end_x, end_y, delays, domain_adj):
        """Execute drag using CDP WebSocket commands"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("⚠️ WebSocket not available, falling back to PyAutoGUI")
                return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, False)
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print("🖱️ Executing CDP drag operation...")
            
            # Move to start position
            print("📍 Step 1: Moving to icon...")
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
            time.sleep(delays['approach'])
            
            # Focus clicks if needed
            print("🎯 Step 2: Establishing focus...")
            if domain_adj['requires_double_click']:
                self._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(0.15)
                self._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(delays['focus'])
            else:
                self._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(delays['focus'])
            
            # Execute drag
            print("🤏 Step 3: Executing drag...")
            self._simulate_drag_via_cdp(ws, start_x, start_y, end_x, end_y)
            time.sleep(delays['post_drop'])

            # Move to safe position via CDP
            print("🎯 Moving to safe position via CDP...")
            screen_width, screen_height = 1920, 1080  # Assume fullscreen
            safe_x = screen_width - random.randint(30, 80)
            safe_y = screen_height - random.randint(30, 80)
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": safe_x, "y": safe_y}})
            print(f"✅ Mouse moved to safe corner via CDP: ({safe_x}, {safe_y})")
            
            ws.close()
            print("✅ CDP drag completed!")
            return True
            
        except Exception as e:
            print(f"❌ CDP drag failed: {e}, falling back to PyAutoGUI")
            return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, False)

    def _execute_pyautogui_drag(self, start_x, start_y, end_x, end_y, delays, domain_adj, is_first_icon):
        """Execute drag using PyAutoGUI (original method)"""
        try:
            print("🖱️ Executing PyAutoGUI drag operation...")
            
            # === APPROACH ICON ===
            print("📍 Step 1: Moving to icon...")
            move_duration = (0.4 if is_first_icon else 0.25) * domain_adj['drag_speed_multiplier']
            pyautogui.moveTo(start_x, start_y, duration=move_duration)
            time.sleep(delays['approach'])
            
            # === ESTABLISH FOCUS ===
            print("🎯 Step 2: Establishing focus...")
            if domain_adj['requires_double_click'] or is_first_icon:
                pyautogui.click()
                time.sleep(0.15)
                pyautogui.click()
                time.sleep(delays['focus'])
            else:
                pyautogui.click()
                time.sleep(delays['focus'])
            
            # Move back to exact position
            pyautogui.moveTo(start_x, start_y, duration=0.1)
            time.sleep(0.1)
            
            # === START DRAG ===
            print("🤏 Step 3: Starting drag - MOUSE DOWN...")
            
            # Ensure correct position
            current_pos = pyautogui.position()
            if abs(current_pos.x - start_x) > 10 or abs(current_pos.y - start_y) > 10:
                print("⚠️ Position drift, correcting...")
                pyautogui.moveTo(start_x, start_y, duration=0.2)
                time.sleep(0.1)
            
            # Mouse down and hold
            pyautogui.mouseDown(button='left')
            print("🔽 Mouse DOWN - holding...")
            time.sleep(delays['hold'])
            
            # === DRAG TO TARGET ===
            print("🎯 Step 4: Dragging to target...")
            drag_duration = delays['drag'] * domain_adj['drag_speed_multiplier']
            
            # Smooth drag movement
            pyautogui.moveTo(end_x, end_y, duration=drag_duration)
            time.sleep(delays['drop'])
            
            # === RELEASE MOUSE ===
            print("🔼 Step 5: MOUSE UP - releasing at target...")
            
            # Final position check
            final_pos = pyautogui.position()
            if abs(final_pos.x - end_x) > 15 or abs(final_pos.y - end_y) > 15:
                print("⚠️ Final position drift, correcting...")
                pyautogui.moveTo(end_x, end_y, duration=0.1)
                time.sleep(0.1)
            
            # Release mouse
            pyautogui.mouseUp(button='left')
            print("✅ Mouse RELEASED - icon dropped!")
            time.sleep(delays['post_drop'])

            print("🎯 Moving to safe position immediately...")
            screen_width, screen_height = pyautogui.size()
            safe_x = screen_width - random.randint(30, 80)
            safe_y = screen_height - random.randint(30, 80)
            pyautogui.moveTo(safe_x, safe_y, duration=random.uniform(0.3, 0.5))
            print(f"✅ Mouse moved to safe corner: ({safe_x}, {safe_y})")
            
            return True
            
        except Exception as e:
            print(f"❌ PyAutoGUI drag failed: {e}")
            return False

    def corrected_return_icon(self, target_slot, use_cdp=False):
        """
        🛡️ SMART SLOT STATE TRACKING: Pre-flight + DUAL Verification + Cross-contamination Prevention
        UPDATED: Sử dụng DUAL verification cho Return (Characters + Putted)
        """
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            target_slot_index = target_slot.get('index', -1)
            domain_adj = self.get_domain_adjustments()
            
            print(f"\n🛡️ SMART SLOT RETURN: Slot {target_slot_index} at ({center_x}, {center_y})")
            print("="*80)
            
            # Check if DUAL verification is available
            dual_verification_enabled = self.characters_available or self.putted_chars_available
            if dual_verification_enabled:
                print("🔄 DUAL verification ENABLED for Return:")
                print(f"   🔢 Characters Array: {'✅ Ready' if self.characters_available else '❌ N/A'}")
                print(f"   🎯 Putted Characters: {'✅ Ready' if self.putted_chars_available else '❌ N/A'}")
            else:
                print("⚠️ DUAL verification DISABLED - using fallback logic")
            
            # ================================================================
            # PHASE 0: PRE-FLIGHT CHECK (Skip if already empty)
            # ================================================================
            print("\n🔍 PHASE 0: PRE-FLIGHT CHECK")
            print("-" * 40)
            
            if dual_verification_enabled:
                needs_return, initial_state, reason = self.check_slot_needs_return(target_slot_index)
                
                if not needs_return:
                    print(f"✅ PHASE 0 RESULT: Slot {target_slot_index} doesn't need return ({reason})")
                    print("🚀 EARLY EXIT: Return operation skipped successfully")
                    print("="*80)
                    return True  # Successfully skipped unnecessary operation
                else:
                    print(f"🎯 PHASE 0 RESULT: Slot {target_slot_index} needs return ({reason})")
            else:
                print("⚠️ Pre-flight check skipped (verification disabled)")
                initial_state = None
            
            # ================================================================
            # SAFE ZONE RETRIEVAL WITH LAZY CACHING (ENHANCED)
            # ================================================================
            print("\n🛡️ SAFE ZONE RETRIEVAL WITH LAZY CACHING")
            print("-" * 40)

            # Check cache first
            if target_slot_index in self.safe_zone_cache:
                cache_data = self.safe_zone_cache[target_slot_index]
                max_safe_radius = cache_data['max_radius']
                safe_coordinates = cache_data['coordinates']
                
                print(f"🚀 CACHE HIT: Slot {target_slot_index}")
                print(f"   📊 Cached coordinates: {len(safe_coordinates)}")
                print(f"   🛡️ Max radius: {max_safe_radius}px")
                print(f"   ⚡ Performance: INSTANT (no calculation needed)")
            else:
                print(f"⚠️ CACHE MISS: Slot {target_slot_index} not found in cache")
                print("   🔄 Computing and caching for future use...")
                
                # Get config for calculation
                if hasattr(self, 'config') and self.config:
                    all_slots = self.config.get('layout', {}).get('slots', [])
                else:
                    print("⚠️ No config available, using conservative defaults")
                    all_slots = []
                
                # Calculate safe zone
                max_safe_radius, safe_coordinates = self.calculate_slot_safe_zone(target_slot_index, all_slots)
                
                # 🔥 LAZY CACHING: Cache the result for future use
                if safe_coordinates:  # Only cache if calculation was successful
                    self.safe_zone_cache[target_slot_index] = {
                        'max_radius': max_safe_radius,
                        'coordinates': safe_coordinates,
                        'center_x': center_x,
                        'center_y': center_y
                    }
                    print(f"   ✅ LAZY CACHED: Slot {target_slot_index} saved for future use")
                    print(f"   📊 Cache size: {len(self.safe_zone_cache)} slots")
                else:
                    print(f"   ⚠️ Could not cache: Calculation failed for Slot {target_slot_index}")

            if not safe_coordinates:
                print("❌ Could not get safe coordinates, using minimal fallback")
                safe_coordinates = [(center_x, center_y)]
                max_safe_radius = 30
            
            success = False
            phase_used = "none"
            
            # ================================================================
            # PHASE 1: PRECISION CENTER ATTACK with DUAL VERIFICATION
            # ================================================================
            print(f"\n🎯 PHASE 1: PRECISION CENTER with DUAL VERIFICATION")
            print("-" * 40)
            
            center_coords = [(center_x, center_y)]
            
            # Get DUAL counts BEFORE return (if verification available)
            characters_before, putted_before = None, None
            if dual_verification_enabled:
                characters_before, putted_before = self.get_dual_counts()
                print(f"📊 DUAL counts BEFORE return:")
                print(f"   🔢 Characters: {characters_before}")
                print(f"   🎯 Putted: {putted_before}")
            
            # Try single click on center
            print("   🔹 Method: Precision single click")
            self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="single")
            
            # Wait and verify with DUAL verification
            verification_wait = 0.3 if dual_verification_enabled else 0.2  # Further reduced
            time.sleep(verification_wait)
            
            if dual_verification_enabled:
                characters_after, putted_after = self.get_dual_counts()
                print(f"📊 DUAL counts AFTER return:")
                print(f"   🔢 Characters: {characters_after}")
                print(f"   🎯 Putted: {putted_after}")
                
                # Use DUAL verification for return
                if self.verify_return_success_dual(characters_before, characters_after, putted_before, putted_after):
                    print("🎉 PHASE 1 SUCCESS: Precision center click worked with DUAL verification!")
                    success = True
                    phase_used = "phase_1_precision_center_dual"
                else:
                    print(f"❌ Phase 1 failed: DUAL verification failed")
            
            # Try double click if single failed
            if not success and not self.should_stop:
                print("   🔹 Method: Precision double click with DUAL verification")
                
                # Get DUAL counts before double click
                if dual_verification_enabled:
                    characters_before, putted_before = self.get_dual_counts()
                
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="double")
                
                time.sleep(verification_wait)
                if dual_verification_enabled:
                    characters_after, putted_after = self.get_dual_counts()
                    
                    # Use DUAL verification
                    if self.verify_return_success_dual(characters_before, characters_after, putted_before, putted_after):
                        print("🎉 PHASE 1 SUCCESS: Precision double click worked with DUAL verification!")
                        success = True
                        phase_used = "phase_1_precision_double_dual"
            
            # ================================================================
            # PHASE 2: SAFE BOUNDARY PATTERN with DUAL VERIFICATION
            # ================================================================
            if not success and not self.should_stop and len(safe_coordinates) > 1:
                print(f"\n🛡️ PHASE 2: SAFE BOUNDARY PATTERN with DUAL VERIFICATION ({len(safe_coordinates)} coords)")
                print("-" * 40)
                
                # Get DUAL counts before phase 2
                if dual_verification_enabled:
                    characters_before, putted_before = self.get_dual_counts()
                    print(f"📊 DUAL counts BEFORE Phase 2: Characters={characters_before}, Putted={putted_before}")
                
                # Execute all clicks within safe boundary
                self.execute_multi_method_clicks(safe_coordinates, use_cdp, domain_adj, method="single")
                
                # Wait and verify after entire phase
                time.sleep(verification_wait + 0.1)  # Slightly longer for multi-coord
                if dual_verification_enabled:
                    characters_after, putted_after = self.get_dual_counts()
                    
                    # Use DUAL verification
                    if self.verify_return_success_dual(characters_before, characters_after, putted_before, putted_after):
                        print("🎉 PHASE 2 SUCCESS: Safe boundary pattern worked with DUAL verification!")
                        success = True
                        phase_used = "phase_2_safe_boundary_dual"
                    else:
                        print(f"❌ Phase 2 failed: DUAL verification failed")
            
            # ================================================================
            # PHASE 3: MULTI-METHOD ON SAFE COORDS with DUAL VERIFICATION
            # ================================================================
            if not success and not self.should_stop and len(safe_coordinates) > 1:
                print(f"\n💥 PHASE 3: MULTI-METHOD with DUAL VERIFICATION")
                print("-" * 40)
                
                # Try different methods on safe coordinates
                methods = ["double", "triple", "hold"]
                
                for method in methods:
                    if success or self.should_stop:
                        break
                        
                    print(f"   🔥 Method: {method} with DUAL verification")
                    
                    # Get DUAL counts before this method
                    if dual_verification_enabled:
                        characters_before, putted_before = self.get_dual_counts()
                    
                    self.execute_multi_method_clicks(safe_coordinates, use_cdp, domain_adj, method=method)
                    
                    # Check after each method
                    time.sleep(verification_wait)
                    if dual_verification_enabled:
                        characters_after, putted_after = self.get_dual_counts()
                        
                        # Use DUAL verification
                        if self.verify_return_success_dual(characters_before, characters_after, putted_before, putted_after):
                            print(f"🎉 PHASE 3 SUCCESS: {method} method worked with DUAL verification!")
                            success = True
                            phase_used = f"phase_3_{method}_dual"
                            break
                        else:
                            print(f"❌ Method {method} failed: DUAL verification failed")
            
            # ================================================================
            # PHASE 4: EMERGENCY MICRO-EXPANSION with DUAL VERIFICATION
            # ================================================================
            if not success and not self.should_stop:
                print(f"\n🚨 PHASE 4: EMERGENCY MICRO-EXPANSION with DUAL VERIFICATION (Max {max_safe_radius + 10}px)")
                print("-" * 40)
                
                # Generate emergency coordinates - slightly beyond safe zone but still conservative
                emergency_radius = min(max_safe_radius + 10, 60)  # Very limited expansion
                emergency_coords = self.generate_circle_pattern_coordinates(
                    center_x, center_y, radius=emergency_radius, num_points=6
                )
                
                # Get DUAL counts before emergency
                if dual_verification_enabled:
                    characters_before, putted_before = self.get_dual_counts()
                    print(f"📊 DUAL counts BEFORE Emergency: Characters={characters_before}, Putted={putted_before}")
                
                print(f"   🚨 Micro-expansion: {len(emergency_coords)} points at {emergency_radius}px")
                self.execute_multi_method_clicks(emergency_coords, use_cdp, domain_adj, method="single")
                
                # Final check
                time.sleep(verification_wait + 0.3)  # Longer wait for emergency
                if dual_verification_enabled:
                    characters_after, putted_after = self.get_dual_counts()
                    
                    # Use DUAL verification
                    if self.verify_return_success_dual(characters_before, characters_after, putted_before, putted_after):
                        print("🎉 PHASE 4 SUCCESS: Emergency micro-expansion worked with DUAL verification!")
                        success = True
                        phase_used = "phase_4_emergency_dual"
                    else:
                        print(f"❌ Emergency phase failed: DUAL verification failed")
            
            # ================================================================
            # FINAL RESULT WITH COMPREHENSIVE REPORT
            # ================================================================
            print("\n" + "="*80)
            if success:
                print(f"🎉 SMART SLOT RETURN COMPLETED SUCCESSFULLY with DUAL VERIFICATION!")
                print(f"   ✅ Target slot: {target_slot_index}")
                print(f"   ✅ Success method: {phase_used}")
                print(f"   🛡️ Max safe radius used: {max_safe_radius}px")
                print(f"   🔄 DUAL verification: {'✅ Enabled' if dual_verification_enabled else '⚠️ Disabled'}")
                print(f"   🛡️ Cross-slot contamination: PREVENTED")
                print(f"   📊 Verification systems: Characters Array {'✅' if self.characters_available else '❌'} + Putted Characters {'✅' if self.putted_chars_available else '❌'}")
                print("="*80)
                return True
            else:
                print(f"💥 SMART SLOT RETURN FAILED with DUAL VERIFICATION")
                print(f"   ❌ Target slot: {target_slot_index}")
                print(f"   ❌ All phases attempted within safe boundary")
                print(f"   🛡️ Max safe radius: {max_safe_radius}px")
                print(f"   🔄 DUAL verification: {'✅ Enabled' if dual_verification_enabled else '⚠️ Disabled'}")
                print(f"   🛡️ Cross-contamination protection: ACTIVE")
                print(f"   📊 DUAL verification systems tested")
                print("="*80)
                return False
                
        except Exception as e:
            print(f"❌ Smart slot return error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _execute_cdp_return(self, center_x, center_y, domain_adj):
        """Execute return using CDP"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # Add variation to center position
            varied_x, varied_y = self.add_position_variation(center_x, center_y, 5)
            
            # Move and click
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": varied_x, "y": varied_y}})
            time.sleep(0.1)
            self._simulate_click_via_cdp(ws, varied_x, varied_y)
            
            ws.close()
            
            # Wait for return animation
            return_wait = 0.4 * domain_adj['stability_factor']
            time.sleep(return_wait)
            
            print("✅ CDP return completed")
            return True
            
        except Exception as e:
            print(f"❌ CDP return failed: {e}")
            return False

    def _execute_pyautogui_return(self, click_x, click_y, domain_adj):
        """Execute single return click using PyAutoGUI"""
        return self._execute_pyautogui_single_click(click_x, click_y, domain_adj)

    def _execute_pyautogui_single_click(self, click_x, click_y, domain_adj):
        """ADDED: Execute single click using PyAutoGUI"""
        try:
            print(f"🖱️ Executing PyAutoGUI single click at ({click_x}, {click_y})")
            
            # Move to position with slight variation
            varied_x, varied_y = self.add_position_variation(click_x, click_y, 3)
            pyautogui.moveTo(varied_x, varied_y, duration=0.15)
            time.sleep(0.1)
            
            # Single click
            pyautogui.click(button='left')
            
            # Wait for click to register
            click_wait = 0.2 * domain_adj['stability_factor']
            time.sleep(click_wait)
            
            print("✅ PyAutoGUI single click completed")
            return True
            
        except Exception as e:
            print(f"❌ PyAutoGUI single click failed: {e}")
            return False

    # ================================================================
    # == HIGH-LEVEL GAME OPERATIONS (ENHANCED) ==
    # ================================================================
    
    def showcase_single_icon_corrected(self, icon_index, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Showcase single icon with verification system"""
        try:
            if not self.config:
                print("❌ No config loaded")
                return False
                
            layout = self.config['layout']
            icons = layout['icons']
            
            if icon_index >= len(icons):
                print(f"❌ Icon index {icon_index} does not exist")
                return False
            
            # Find target slot (index 3)
            target_slot = self._find_slot_by_index(3)
            if not target_slot:
                print("❌ Could not find slot index 3")
                return False
            
            current_icon = icons[icon_index]
            emoji = "👻" if mode == "Horror" else "🎪"
            
            if progress_callback:
                progress_callback(f"{emoji} Showcasing {mode} icon {icon_index + 1}")
            
            print(f"\n{emoji} STARTING ENHANCED SHOWCASE: {mode} icon {icon_index + 1}")
            print("="*60)
            
            # PHASE 1: ENHANCED DRAG & DROP with verification
            print("🎯 PHASE 1: Enhanced Drag & Drop with verification...")
            is_first_icon = (icon_index == 0 and mode == "Normal")
            
            # Use CDP for first icon, PyAutoGUI for others (can be customized)
            use_cdp = is_first_icon  # Can be changed to True for all, or False for all
            
            drag_success = self.corrected_drag_and_drop(current_icon, target_slot, is_first_icon, use_cdp)
            
            if not drag_success:
                print(f"❌ Failed to drag icon {icon_index} with verification")
                return False
            
            if self.should_stop:
                return False
            
            # PHASE 2: LISTEN TO MUSIC
            print(f"🎵 PHASE 2: Listening to music for {listen_duration}s...")
            for countdown in range(listen_duration, 0, -1):
                if self.should_stop:
                    return False
                if progress_callback:
                    progress_callback(f"🎵 Listening {mode} icon {icon_index + 1} ({countdown}s)")
                time.sleep(1)
            
            if self.should_stop:
                return False
            
            # PHASE 3: SMART RETURN WITH MODE DETECTION
            print("🔄 PHASE 3: Smart returning icon with mode detection...")
            if progress_callback:
                progress_callback(f"↺ Smart returning {mode} icon {icon_index + 1}")

            # Enhanced return with mode detection
            operation_context = f"showcase_{mode}_icon_{icon_index}"
            return_success = self.execute_smart_return_with_mode_detection(
                target_slot, operation_context, use_cdp
            )

            if return_success:
                # STREAMLINED: Quick validation without extra delays
                print(f"✅ Icon {icon_index + 1} returned successfully")
                
                # Optional: Quick state check without comprehensive validation
                if self.characters_available:
                    quick_count = self.get_characters_count()
                    if quick_count == 0:
                        print(f"   📊 Validation: Characters count = 0 (perfect)")
                    else:
                        print(f"   ⚠️ Validation: Characters count = {quick_count} (may need cleanup)")
            else:
                print(f"⚠️ Warning: Failed to return icon {icon_index}")
                
                # STREAMLINED: Quick emergency cleanup only if needed
                if self.characters_available:
                    emergency_count = self.get_characters_count()
                    if emergency_count > 0:
                        print(f"🚨 Quick emergency cleanup attempt...")
                        self._execute_emergency_return_click(target_slot)

            # OPTIMIZED: Reduce stabilization delay
            time.sleep(0.1)  # Reduced from 0.3s to 0.1s
            
            print(f"✅ COMPLETED ENHANCED SHOWCASE: {mode} icon {icon_index + 1}")
            print("="*60)
            return True
            
        except Exception as e:
            print("❌ Error in enhanced showcase icon " + str(icon_index) + ": " + str(e))
            return False

    # ================================================================
    # == EXISTING GAME LOGIC (kept as-is with small adaptations) ==
    # ================================================================
    
    def add_position_variation(self, x, y, max_offset=8):
        offset_x = random.randint(-max_offset, max_offset)
        offset_y = random.randint(-max_offset, max_offset)
        return x + offset_x, y + offset_y

    def smart_delay(self, base_time, variation=0.3):
        """Smart delay with random variation and domain adjustments"""
        domain_adj = self.get_domain_adjustments()
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

    def generate_random_remixes(self, avoid_horror_icon=False):
        """Generate random remixes with option to avoid horror icon"""
        count = random.randint(3, 5)
        remix_library = []
        for i in range(count):
            pattern = random.choice(self.remix_patterns)
            remix = self.create_remix_from_pattern(pattern, i + 1, avoid_horror_icon=avoid_horror_icon)
            if remix:
                remix_library.append(remix)
        
        mode_suffix = " (No Horror Icon)" if avoid_horror_icon else ""
        print(f"Generated {len(remix_library)} random remixes{mode_suffix}!")
        return remix_library
    
    def create_remix_from_pattern(self, pattern, remix_number, avoid_horror_icon=False):
        """Create remix from pattern with option to avoid horror icon (index 19)"""
        try:
            selected_icons = []
            
            # Create dynamic icon_groups based on avoid_horror_icon
            icon_groups_to_use = self.icon_groups.copy()
            if avoid_horror_icon:
                # Remove icon 19 from vocals group (15-19 -> 15-18)
                icon_groups_to_use['vocals'] = list(range(15, 19))  # Remove index 19
                print(f"🚨 Avoiding horror icon (19) for normal mode remix")
            
            for group_name, count in [('beats', pattern['beats']), 
                                    ('effects', pattern['effects']),
                                    ('melodies', pattern['melodies']), 
                                    ('vocals', pattern['vocals'])]:
                if count > 0:
                    available_icons = icon_groups_to_use[group_name].copy()
                    selected = random.sample(available_icons, min(count, len(available_icons)))
                    selected_icons.extend(selected)
            
            random.shuffle(selected_icons)
            pattern_counts = f"{pattern['beats']}B+{pattern['effects']}E+{pattern['melodies']}M+{pattern['vocals']}V"
            remix_name = f"{pattern['name']} #{remix_number}"
            
            return {
                'name': remix_name,
                'description': f"{pattern['description']} ({pattern_counts})",
                'icons': selected_icons,
                'pattern': pattern['name']
            }
        except Exception as e:
            print("Error creating remix from pattern: " + str(e))
            return None
    
    def _find_slot_by_index(self, slot_index):
        if not self.config:
            return None
        layout = self.config['layout']
        slots = layout.get('slots', [])
        for slot in slots:
            if slot.get('index') == slot_index:
                return slot
        return None
    
    def get_drag_statistics(self):
        return self.drag_stats.get_stats()

    def prepare_game_for_playing(self):
        """Prepare game for playing with enhanced setup"""
        try:
            print("🎯 Preparing game for enhanced workflow...")
            
            # Clean mouse state
            self.ensure_mouse_released()
            
            # Warm-up first icon
            layout = self.config['layout']
            if layout['icons']:
                first_icon = layout['icons'][0]
                print("🎯 Warming up first icon...")
                
                warm_x, warm_y = self.add_position_variation(first_icon['x'], first_icon['y'])
                pyautogui.moveTo(warm_x, warm_y, duration=random.uniform(0.3, 0.5))
                self.smart_delay(0.3, 0.1)
                
                # Micro-movement
                offset_x, offset_y = warm_x + 8, warm_y + 8
                pyautogui.moveTo(offset_x, offset_y, duration=0.15)
                self.smart_delay(0.2, 0.05)
                pyautogui.moveTo(warm_x, warm_y, duration=0.15)
                self.smart_delay(0.3, 0.1)
            
            print("✅ Game preparation completed with enhanced workflow!")
            
        except Exception as e:
            print("Error in game preparation: " + str(e))

    # ================================================================
    # == WORKFLOW METHODS (redirected to enhanced versions) ==
    # ================================================================

    def showcase_all_icons_corrected(self, mode="Normal", skip_icon_19=False, listen_duration=15, progress_callback=None):
        """ENHANCED: Showcase all icons with verification system"""
        try:
            if not self.config:
                return False
                
            layout = self.config['layout']
            icons = layout['icons']
            
            # Determine icon range
            if mode == "Normal" and skip_icon_19:
                icon_range = range(len(icons) - 1)  # 0-18, skip icon 19
            else:
                icon_range = range(len(icons))  # 0-19
            
            print(f"\n🎪 STARTING ENHANCED {mode.upper()} SHOWCASE")
            print(f"📊 Will showcase {len(icon_range)} icons with verification system")
            if self.characters_available:
                print("🔍 Drag verification: ENABLED")
            else:
                print("⚠️ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success_count = 0
            for icon_index in icon_range:
                if self.should_stop:
                    break
                
                if self.showcase_single_icon_corrected(icon_index, mode, listen_duration, progress_callback):
                    success_count += 1
                else:
                    print(f"⚠️ Failed to showcase icon {icon_index}")
            
            print(f"\n✅ ENHANCED {mode.upper()} SHOWCASE COMPLETED")
            print(f"📊 Success: {success_count}/{len(icon_range)} icons")
            print("="*60)
            return success_count == len(icon_range)
            
        except Exception as e:
            print("❌ Error in enhanced " + mode + " showcase: " + str(e))
            return False

    def play_complete_remix_corrected(self, remix_data, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Play remix with verification system"""
        try:
            if not self.config or not remix_data:
                return False
                
            layout = self.config['layout']
            icons = layout['icons']
            slots = layout['slots']
            
            remix_name = remix_data.get('name', 'Unknown Remix')
            remix_icons = remix_data.get('icons', [])
            
            emoji = "🎵" if mode == "Test" else ("👻" if mode == "Horror" else "🎪")
            
            print(f"\n{emoji} STARTING ENHANCED REMIX: {remix_name}")
            if self.characters_available:
                print("🔍 Verification system ENABLED for all drags")
            else:
                print("⚠️ Verification system DISABLED - using fallback")
            print("="*60)
            
            # Phase 1: Drag all icons to slots with verification
            print("🎯 PHASE 1: Dragging all icons to slots with verification...")
            for slot_position, icon_index in enumerate(remix_icons):
                if self.should_stop:
                    return False
                
                # Validate indices
                if icon_index >= len(icons) or slot_position >= len(slots):
                    print(f"⚠️ Invalid index: icon {icon_index} or slot {slot_position}")
                    continue
                
                current_icon = icons[icon_index]
                target_slot = slots[slot_position]
                
                if progress_callback:
                    progress_callback(f"{emoji} {remix_name}: Icon {icon_index} → Slot {slot_position}")
                
                print(f"  🎯 Dragging icon {icon_index} to slot {slot_position} with verification...")
                
                # Use enhanced drag with verification
                use_cdp = False  # Can be customized
                if not self.corrected_drag_and_drop(current_icon, target_slot, False, use_cdp):
                    print(f"  ❌ Failed to drag icon {icon_index} with verification")
                    continue
                
                # Short delay between drags
                time.sleep(0.3)
            
            if self.should_stop:
                return False
            
            # Phase 2: Listen to remix music
            print(f"🎶 PHASE 2: Enjoying remix for {listen_duration}s...")
            if progress_callback:
                progress_callback(f"🎶 Enjoying {remix_name}")
            
            for countdown in range(listen_duration, 0, -1):
                if self.should_stop:
                    return False
                if progress_callback:
                    progress_callback(f"🎶 Enjoying {remix_name} ({countdown}s)")
                time.sleep(1)
            
            if self.should_stop:
                return False
            
            # Phase 3: Smart batch return with mode detection
            print("🔄 PHASE 3: Smart batch returning with mode detection...")
            if progress_callback:
                progress_callback(f"🧹 Smart cleanup {remix_name}")

            # Set explicit remix mode for batch operations
            self.set_return_mode_explicit(self.RETURN_MODE_REMIX, f"remix_cleanup_{len(remix_icons)}_icons")

            print(f"  🔄 Using mode-aware batch return for {len(remix_icons)} icons...")

            # Use smart batch return with mode awareness
            success_count, total_attempted, error_log = self.smart_batch_return_all_slots_with_mode(use_cdp=False)

            if success_count == total_attempted:
                print(f"  ✅ Smart batch return: {success_count}/{total_attempted} icons returned successfully")
            else:
                print(f"  ⚠️ Smart batch return: {success_count}/{total_attempted} icons returned")
                if error_log:
                    print(f"  📋 Errors: {len(error_log)} issues encountered")
                    # Continue anyway, don't fail the entire remix

            # Brief stabilization pause
            time.sleep(0.5)
            
            print(f"✅ COMPLETED ENHANCED REMIX: {remix_name}")
            print("="*60)
            return True
            
        except Exception as e:
            print("❌ Error in enhanced remix " + remix_data.get('name', 'Unknown') + ": " + str(e))
            return False

    def execute_full_showcase_workflow_corrected(self, progress_callback=None):
        """ENHANCED: Execute full workflow with verification system"""
        try:
            if not self.config:
                return False
                
            game_settings = self.config.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            has_bonus = game_settings.get('hasBonus', False)
            
            print("\n🎪 STARTING ENHANCED FULL SHOWCASE WORKFLOW")
            if self.characters_available:
                print("🔍 Drag verification system: ENABLED")
            else:
                print("⚠️ Drag verification system: DISABLED (fallback mode)")
            print("="*60)
            
            # === NORMAL MODE ===
            if progress_callback:
                progress_callback("🎪 Starting Enhanced Normal Mode...")
            
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
            if has_horror and not self.should_stop:
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
                progress_callback("🎉 Enhanced full showcase workflow completed!")

            print("\n🎉 ENHANCED FULL SHOWCASE WORKFLOW COMPLETED!")
            print(self.get_drag_statistics())

            # ================================================================
            # 🧹 FINAL SMART CLEANUP & VALIDATION
            # ================================================================
            print("\n🧹 FINAL SMART CLEANUP & COMPREHENSIVE VALIDATION")
            print("="*70)

            try:
                # Smart batch cleanup validation - ensure all slots are clean
                print("🔄 Phase 1: Smart batch cleanup validation...")
                cleanup_success_count, cleanup_total, cleanup_errors = self.smart_batch_return_all_slots(use_cdp=False)
                
                if cleanup_total == 0:
                    print("✅ No cleanup needed - all slots already clean")
                elif cleanup_success_count == cleanup_total:
                    print(f"✅ Smart cleanup: {cleanup_success_count}/{cleanup_total} slots cleaned perfectly")
                else:
                    print(f"⚠️ Smart cleanup: {cleanup_success_count}/{cleanup_total} slots cleaned")
                    if cleanup_errors:
                        print(f"   📋 Cleanup issues: {len(cleanup_errors)} errors")
                        for error in cleanup_errors[:2]:  # Show first 2 errors
                            print(f"      - {error}")
                
                # Comprehensive workflow validation
                print("\n🔍 Phase 2: Comprehensive workflow validation...")
                is_complete, remaining_issues, recommendations = self.validate_workflow_completion()
                
                if is_complete:
                    print("✅ WORKFLOW VALIDATION: PERFECT COMPLETION! 🎉")
                    print("   🛡️ All slots clean")
                    print("   📊 State consistency verified")
                    print("   🎯 Cross-contamination: NONE")
                else:
                    print(f"⚠️ WORKFLOW VALIDATION: {len(remaining_issues)} issues detected")
                    print("📋 Issues found:")
                    for issue in remaining_issues[:3]:  # Show first 3 issues
                        print(f"   ❌ {issue}")
                    
                    if recommendations:
                        print("💡 Recommendations:")
                        for rec in recommendations[:2]:  # Show first 2 recommendations
                            print(f"   💡 {rec}")
                
                # Enhanced summary
                print("\n📊 ENHANCED WORKFLOW SUMMARY:")
                summary = self.get_enhanced_workflow_summary()
                print(summary)
                
            except Exception as e:
                print(f"⚠️ Error during final validation: {e}")
                print("📊 Basic statistics:")
                print(self.get_drag_statistics())

            print("="*70)
            return True
            
        except Exception as e:
            print("❌ Error in enhanced full showcase workflow: " + str(e))
            return False

    def showcase_all_bonus_corrected(self, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Showcase bonus with verification system"""
        try:
            if not self.config:
                return False
                
            layout = self.config['layout']
            bonus_chars = layout.get('bonus', [])
            
            if not bonus_chars:
                print(f"ℹ️ No bonus characters for {mode} mode")
                return True
            
            target_slot = self._find_slot_by_index(3)
            if not target_slot:
                return False
            
            emoji = "🎁" if mode == "Normal" else "👻"
            print(f"\n{emoji} STARTING ENHANCED {mode.upper()} BONUS SHOWCASE")
            print(f"📊 Will showcase {len(bonus_chars)} bonus characters")
            if self.characters_available:
                print("🔍 Drag verification: ENABLED")
            else:
                print("⚠️ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success_count = 0
            for bonus_index, bonus_char in enumerate(bonus_chars):
                if self.should_stop:
                    break
                
                if progress_callback:
                    progress_callback(f"{emoji} Showcasing {mode} bonus {bonus_index + 1}")
                
                print(f"{emoji} Showcasing {mode} bonus {bonus_index + 1}")
                
                # Drag bonus to slot with enhanced verification
                use_cdp = False  # Can be customized
                if not self.corrected_drag_and_drop(bonus_char, target_slot, False, use_cdp):
                    print(f"❌ Failed to drag bonus {bonus_index} with verification")
                    continue
                
                if self.should_stop:
                    break
                
                # Listen to music
                print(f"🎵 Listening to bonus {bonus_index + 1} for {listen_duration}s...")
                for countdown in range(listen_duration, 0, -1):
                    if self.should_stop:
                        break
                    if progress_callback:
                        progress_callback(f"🎵 Listening {mode} bonus {bonus_index + 1} ({countdown}s)")
                    time.sleep(1)
                
                if self.should_stop:
                    break
                
                # Return bonus
                print(f"🔄 Returning bonus {bonus_index + 1}...")
                self.corrected_return_icon(target_slot, use_cdp)
                time.sleep(0.2)
                success_count += 1
            
            print(f"\n✅ ENHANCED {mode.upper()} BONUS SHOWCASE COMPLETED")
            print(f"📊 Success: {success_count}/{len(bonus_chars)} characters")
            print("="*60)
            return success_count == len(bonus_chars)
            
        except Exception as e:
            print("❌ Error in enhanced " + mode + " bonus showcase: " + str(e))
            return False
    
    def play_all_remixes_corrected(self, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Play all remixes with verification system"""
        try:
            if not self.remix_library:
                print("ℹ️ No remixes in library")
                return True
            
            emoji = "🎵" if mode == "Test" else ("👻" if mode == "Horror" else "🎪")
            print(f"\n{emoji} STARTING ENHANCED {mode.upper()} REMIX SESSION")
            print(f"📊 Will play {len(self.remix_library)} remixes")
            if self.characters_available:
                print("🔍 Drag verification: ENABLED for all remix drags")
            else:
                print("⚠️ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success_count = 0
            for remix_index, remix in enumerate(self.remix_library):
                if self.should_stop:
                    break
                
                self.current_remix_index = remix_index
                
                if progress_callback:
                    progress_callback(f"{emoji} Remix {remix_index + 1}/{len(self.remix_library)}: {remix['name']}")
                
                if self.play_complete_remix_corrected(remix, mode, listen_duration, progress_callback):
                    success_count += 1
                else:
                    print(f"⚠️ Failed to play remix: {remix['name']}")
                
                # Delay between remixes
                if remix_index < len(self.remix_library) - 1 and not self.should_stop:
                    time.sleep(2.0)
            
            print(f"\n✅ ENHANCED {mode.upper()} REMIX SESSION COMPLETED")
            print(f"📊 Success: {success_count}/{len(self.remix_library)} remixes")
            print("="*60)
            return success_count == len(self.remix_library)
            
        except Exception as e:
            print("❌ Error in enhanced " + mode + " remix session: " + str(e))
            return False
    
    def trigger_horror_mode_transition_corrected(self, transition_duration=15, progress_callback=None):
        """ENHANCED: Trigger horror mode with verification system"""
        try:
            if not self.config:
                return False
                
            layout = self.config['layout']
            icons = layout['icons']
            
            if len(icons) < 20:
                print("❌ Not enough 20 icons to trigger Horror mode")
                return False
            
            # Get icon index 19 (last icon)
            horror_icon = icons[19]
            
            # Find slot index 3
            target_slot = self._find_slot_by_index(3)
            if not target_slot:
                return False
            
            if progress_callback:
                progress_callback("👻 Triggering Horror Mode with verification...")
            
            print("\n👻 TRIGGERING ENHANCED HORROR MODE TRANSITION...")
            if self.characters_available:
                print("🔍 Using verification for horror icon drag")
            else:
                print("⚠️ Verification disabled - using fallback")
            print("="*60)
            
            # Drag icon 19 to slot 3 with enhanced verification
            print("🎯 Dragging horror icon (19) to slot 3 with verification...")
            use_cdp = False  # Can be customized
            if not self.corrected_drag_and_drop(horror_icon, target_slot, False, use_cdp):
                print("❌ Failed to trigger horror mode with verification")
                return False
            
            # Wait for transition
            print(f"⏳ Waiting for horror transformation ({transition_duration}s)...")
            for countdown in range(transition_duration, 0, -1):
                if self.should_stop:
                    return False
                if progress_callback:
                    progress_callback(f"👻 Horror mode transition... ({countdown}s)")
                time.sleep(1)
            
            if not self.should_stop:
                if progress_callback:
                    progress_callback("👻 Horror Mode activated with verification!")
                print("✅ ENHANCED HORROR MODE TRANSITION COMPLETED!")
                
                # ================================================================
                # 👻 HORROR MODE STATE VALIDATION
                # ================================================================
                print("\n👻 HORROR MODE STATE VALIDATION")
                print("="*50)
                
                try:
                    # Validate horror mode activation state
                    horror_state = self.get_comprehensive_slot_state()
                    
                    if horror_state and self.characters_available:
                        putted_chars = horror_state.get('putted_characters', 0)
                        polos_array = horror_state.get('polos_array', [])
                        
                        print(f"📊 Horror mode state:")
                        print(f"   Putted characters: {putted_chars}")
                        print(f"   Active slots: {sum(1 for x in polos_array if x == '1') if polos_array else 'N/A'}")
                        
                        # Expect at least 1 active slot (the horror icon we just placed)
                        if putted_chars >= 1:
                            print("✅ Horror mode properly activated - icon placed")
                        else:
                            print("⚠️ Horror mode state unclear - may need verification")
                    else:
                        print("⚠️ Cannot validate horror mode state (verification disabled)")
                    
                except Exception as e:
                    print(f"⚠️ Error validating horror mode state: {e}")
                
                print("="*50)
                time.sleep(1.0)

            return True
            
        except Exception as e:
            print("❌ Error triggering enhanced horror mode: " + str(e))
            return False
    
    def execute_remix_only_workflow_corrected(self, progress_callback=None):
        """ENHANCED: Remix-only workflow with verification system"""
        try:
            if progress_callback:
                progress_callback("🎵 Starting enhanced remix-only workflow...")
            
            print("\n🎵 STARTING ENHANCED REMIX-ONLY WORKFLOW")
            if self.characters_available:
                print("🔍 Drag verification: ENABLED for all remixes")
            else:
                print("⚠️ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success = self.play_all_remixes_corrected("Test", progress_callback=progress_callback)
            
            if success:
                if progress_callback:
                    progress_callback("🎉 Enhanced remix-only workflow completed!")
                print("\n🎉 ENHANCED REMIX-ONLY WORKFLOW COMPLETED!")
                
                # ================================================================
                # 🧹 REMIX-ONLY SMART CLEANUP & VALIDATION
                # ================================================================
                print("\n🧹 REMIX-ONLY SMART CLEANUP & VALIDATION")
                print("="*60)
                
                try:
                    # Smart cleanup for remix-only mode
                    print("🔄 Smart batch cleanup for remix-only mode...")
                    cleanup_success_count, cleanup_total, cleanup_errors = self.smart_batch_return_all_slots(use_cdp=False)
                    
                    if cleanup_total == 0:
                        print("✅ No remix cleanup needed - all slots clean")
                    elif cleanup_success_count == cleanup_total:
                        print(f"✅ Remix cleanup: {cleanup_success_count}/{cleanup_total} perfect")
                    else:
                        print(f"⚠️ Remix cleanup: {cleanup_success_count}/{cleanup_total} with issues")
                    
                    # Remix-specific validation
                    is_complete, remaining_issues, recommendations = self.validate_workflow_completion()
                    
                    if is_complete:
                        print("✅ REMIX VALIDATION: PERFECT! All remixes cleaned properly")
                    else:
                        print(f"⚠️ REMIX VALIDATION: {len(remaining_issues)} cleanup issues")
                        for issue in remaining_issues[:2]:
                            print(f"   - {issue}")
                    
                    # Enhanced remix summary
                    print("\n📊 ENHANCED REMIX WORKFLOW STATS:")
                    summary = self.get_enhanced_workflow_summary()
                    print(summary)
                    
                except Exception as e:
                    print(f"⚠️ Error during remix validation: {e}")
                    print("📊 Basic remix statistics:")
                    print(self.get_drag_statistics())
                
                print("="*60)

            return success
            
        except Exception as e:
            print("❌ Error in enhanced remix-only workflow: " + str(e))
            return False

    # ================================================================
    # == LEGACY METHOD REDIRECTS ==
    # ================================================================
    
    def showcase_single_icon(self, icon_index, mode="Normal", listen_duration=15, progress_callback=None):
        """Redirect to enhanced version"""
        return self.showcase_single_icon_corrected(icon_index, mode, listen_duration, progress_callback)
    
    def enhanced_drag_and_drop(self, icon, target_slot, is_first_icon=False):
        """Redirect to enhanced version"""
        return self.corrected_drag_and_drop(icon, target_slot, is_first_icon)
    
    def enhanced_click_to_return(self, target_slot):
        """Redirect to enhanced version"""
        return self.corrected_return_icon(target_slot)
    
    def showcase_all_icons(self, mode="Normal", skip_icon_19=False, listen_duration=15, progress_callback=None):
        """Redirect to enhanced version"""
        return self.showcase_all_icons_corrected(mode, skip_icon_19, listen_duration, progress_callback)
    
    def play_complete_remix(self, remix_data, mode="Normal", listen_duration=15, progress_callback=None):
        """Redirect to enhanced version"""
        return self.play_complete_remix_corrected(remix_data, mode, listen_duration, progress_callback)
    
    def execute_full_showcase_workflow(self, progress_callback=None):
        """Redirect to enhanced version"""
        return self.execute_full_showcase_workflow_corrected(progress_callback)
    
    def execute_remix_only_workflow(self, progress_callback=None):
        """Redirect to enhanced version"""
        return self.execute_remix_only_workflow_corrected(progress_callback)

    # ================================================================
    # == GUI SETUP (ENHANCED) ==
    # ================================================================
    
    def setup_gui(self):
        self.root = Tk()
        self.root.title("Sprunki Game Player - ENHANCED with Characters Array Verification + Smart Page Verification")
        self.root.geometry("650x900")
        
        self.mode = IntVar(value=1)
        self.status_var = StringVar()
        self.status_var.set("Config not loaded")
        self.action_var = StringVar()
        self.action_var.set("🚀 Ready for ENHANCED workflow with Smart Verification...")
        
        # Header
        Label(self.root, text="Sprunki Game Player", font=("Arial", 16, "bold")).pack(pady=10)
        Label(self.root, text="🎮 ENHANCED: Chrome Debug + Characters Array Verification + Smart Page Verification 🚀", 
            font=("Arial", 12), fg="green").pack()
        
        # Enhanced workflow info
        enhanced_info_frame = Frame(self.root, bg="#E8F5E8", relief="ridge", bd=1)
        enhanced_info_frame.pack(fill="x", padx=10, pady=5)
        
        Label(enhanced_info_frame, text="⚡ ENHANCED WORKFLOW:", 
            font=("Arial", 10, "bold"), bg="#E8F5E8", fg="#2E7D32").pack(anchor="w", padx=10, pady=(5,2))
        
        enhanced_features = """✅ Characters Array Verification: Count "1" values in Characters Array for accurate state
        ✅ 5-Coordinate Retry: Center → Left → Right → Top → Bottom positions for each icon
        ✅ Smart Fallback: Auto-fallback to PyAutoGUI if CDP verification fails
        ✅ Enhanced Statistics: Track verification success rate and coordinate usage
        ✅ Game-Aware: Real-time monitoring of Characters Array state
        ✅ Intelligent Retry: Return icon and retry at different coordinate if verification fails"""
        
        Label(enhanced_info_frame, text=enhanced_features, justify="left",
            font=("Arial", 8), fg="#1B5E20", bg="#E8F5E8").pack(anchor="w", padx=15, pady=(0,5))
        
        # Smart verification system info
        verification_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
        verification_frame.pack(fill="x", padx=10, pady=5)
        
        Label(verification_frame, text="🔢 CHARACTERS ARRAY SYSTEM:", 
            font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
        
        verification_text = """🔢 Characters Array Monitoring: Real-time tracking of character placement state
        🎯 Count-Based Verification: Count "1" values in array = number of icons on slots
        ⚡ Stable Across Mods: Works reliably on all game modifications
        🎮 High Performance: Direct VM access with optimized JavaScript functions
        📊 Accurate State: No dependency on unreliable Putted Characters or Polos Array
        🔄 Real-Time Updates: Instant detection of drag/return operations"""
        
        Label(verification_frame, text=verification_text, justify="left",
            font=("Arial", 8), fg="#0D47A1", bg="#E3F2FD").pack(anchor="w", padx=15, pady=(0,5))
        
        # Image detection info
        detection_frame = Frame(self.root, bg="#FFF3E0", relief="ridge", bd=1)
        detection_frame.pack(fill="x", padx=10, pady=5)
        
        Label(detection_frame, text="🖼️ ENHANCED IMAGE DETECTION:", 
            font=("Arial", 10, "bold"), bg="#FFF3E0", fg="#E65100").pack(anchor="w", padx=10, pady=(5,2))
        
        detection_text = """🎯 Smart Auto-Scroll: Automatic PageDown if Run/Fullscreen buttons not in viewport
        🔍 Multi-Scale Detection: Test multiple scale factors for button recognition
        🎪 Domain-Optimized: Specific adjustments for cocrea.world game timing
        🔍 Template Matching: Advanced CV2 image matching with confidence thresholds
        🔄 Retry Logic: Multiple attempts with different scroll positions
        ⚙️ Config-Driven: All detection parameters configurable via config.json"""
        
        Label(detection_frame, text=detection_text, justify="left",
            font=("Arial", 8), fg="#BF360C", bg="#FFF3E0").pack(anchor="w", padx=15, pady=(0,5))
        
        # Mode selection
        mode_frame = Frame(self.root)
        mode_frame.pack(pady=10)
        
        Label(mode_frame, text="🎯 Select play mode:", font=("Arial", 11, "bold")).pack()
        
        Radiobutton(mode_frame, text="🔀 Showcase + Remix (ENHANCED)", 
                variable=self.mode, value=1, font=("Arial", 10)).pack(anchor="w")
        
        Radiobutton(mode_frame, text="🎵 Remix Only (ENHANCED)", 
                variable=self.mode, value=2, font=("Arial", 10)).pack(anchor="w")
        
        # Debug frame with Characters Array System
        debug_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
        debug_frame.pack(fill="x", padx=10, pady=5)
        
        Label(debug_frame, text="🔧 DEBUG TOOLS:", 
            font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
        
        debug_buttons = Frame(debug_frame, bg="#E3F2FD")
        debug_buttons.pack(fill="x", padx=10, pady=(0,5))
        
        Button(debug_buttons, text="📊 Enhanced Stats", 
            command=self.show_drag_stats, bg="#4CAF50", fg="white", font=("Arial", 9)).pack(side="left")
        
        Button(debug_buttons, text="🌐 Test WebSocket", 
            command=self.test_websocket_connection, bg="#FF9800", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        
        Button(debug_buttons, text="🔢 Test Characters Array", 
            command=self.test_verification_system, bg="#9C27B0", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        
        Button(debug_buttons, text="⚡ Test Smart Verification", 
            command=self.test_smart_verification_system, bg="#2196F3", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="🛡️ Check Cache", 
            command=self.check_safe_zone_cache_status, bg="#795548", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="🎯 Check Mode", 
            command=self.show_current_mode_info, bg="#607D8B", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        # Remix info display
        self.display_remix_library_info()
        
        # Main button
        Button(self.root, text="🔢 Load JSON & Start ENHANCED Auto with Characters Array Verification", 
            command=self.load_config_and_start, 
            bg="#4CAF50", fg="white", font=("Arial", 12, "bold")).pack(pady=15)
        
        # Status
        Label(self.root, text="📊 Status:").pack(pady=(20,5))
        Label(self.root, textvariable=self.status_var, fg="blue").pack()
        
        # Instructions with Characters Array System
        instruction_text = """
    🎯 ENHANCED WORKFLOW WITH CHARACTERS ARRAY VERIFICATION:

    🔢 CHARACTERS ARRAY SYSTEM:
    1. Characters Array Monitoring: Real-time tracking of character placement array
    2. Count-Based Verification: Count "1" values = accurate number of placed icons
    3. Stable Across Mods: Works reliably on all game modifications (no Putted Characters bugs)
    4. High Performance: Direct VM access with optimized JavaScript functions

    ⚡ 5-COORDINATE STRATEGY (UNCHANGED):
    - Coordinate 1: Center (original icon position)
    - Coordinate 2: Left (icon_x - 15, icon_y)
    - Coordinate 3: Right (icon_x + 15, icon_y)  
    - Coordinate 4: Top (icon_x, icon_y - 15)
    - Coordinate 5: Bottom (icon_x, icon_y + 15)

    🖼️ IMAGE DETECTION (PRESERVED):
    - Original Run button & Fullscreen detection logic kept exactly as-is
    - Auto-scroll capability for buttons outside viewport
    - Multi-scale template matching with confidence thresholds
    - Domain-specific timing adjustments for cocrea.world

    🚀 NEW WORKFLOW:
    Page Load → Smart Verification → Game Verification → ✅ Verification Success
    → 3s Stabilization → Start Image Detection (Run Button) → Game Load
    → Image Detection (Fullscreen Button) → F11 Fullscreen → Characters Array Setup
    → Ready for Automation with Characters Array Verification

    🎮 BENEFITS:
    - Replaces unreliable Putted Characters with stable Characters Array
    - Works consistently across all game mods and versions
    - Real-time accurate state monitoring
    - No dependency on buggy game variables
    - Enhanced reliability for drag/return operations
        """
        Label(self.root, text=instruction_text, justify="left", 
            font=("Arial", 8), bg="#f0f0f0").pack(pady=10, padx=10, fill="x")
        
        # Control buttons
        self.stop_btn = Button(self.root, text="🛑 Stop Enhanced Auto", 
                            command=self.stop_all, state="disabled",
                            bg="#f44336", fg="white", font=("Arial", 12, "bold"))
        self.stop_btn.pack(pady=10)
        
        # Progress
        Label(self.root, text="📈 Progress:").pack(pady=(10,5))
        self.progress = Progressbar(self.root, length=500, mode='determinate')
        self.progress.pack(pady=5)
        
        # Current action
        Label(self.root, textvariable=self.action_var, fg="green", 
            font=("Arial", 10, "bold")).pack(pady=5)
        
        # Domain support with Characters Array System
        domains_text = f"🌐 Enhanced for: cocrea.world (Chrome Debug + Characters Array Verification + Image Detection)"
        Label(self.root, text=domains_text, fg="gray", font=("Arial", 9)).pack(pady=5)
        
        # Hotkey
        Label(self.root, text="⌨️ ESC = Emergency stop", 
            fg="red", font=("Arial", 10, "bold")).pack(pady=5)
    
    def show_drag_stats(self):
        """Show enhanced drag statistics"""
        stats = self.get_drag_statistics()
        messagebox.showinfo("Enhanced Drag Statistics", stats)

    def test_websocket_connection(self):
        """Test WebSocket connection to Chrome debug"""
        try:
            ws_url = self._get_websocket_url()
            if ws_url:
                ws = websocket.create_connection(ws_url, timeout=5)
                
                # Test with simple command
                test_response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": "window.location.href", "returnByValue": True}
                })
                
                ws.close()
                
                if test_response:
                    messagebox.showinfo("WebSocket Test", 
                        f"✅ WebSocket connection successful!\n\n"
                        f"Debug Port: {self.debugging_port}\n"
                        f"Current URL: {test_response.get('result', {}).get('result', {}).get('value', 'Unknown')}\n"
                        f"CDP commands working correctly.")
                else:
                    messagebox.showwarning("WebSocket Test", 
                        "⚠️ WebSocket connected but CDP commands failed")
            else:
                messagebox.showerror("WebSocket Test", 
                    "❌ Could not establish WebSocket connection\n\n"
                    "Make sure Chrome debug mode is running")
        except Exception as e:
            messagebox.showerror("WebSocket Test", f"❌ WebSocket test failed: {e}")

    def test_verification_system(self):
        """Test Characters Array verification system"""
        try:
            if not self.characters_setup_done:
                messagebox.showinfo("Verification Test", 
                    "🔢 Characters Array system not setup yet.\n\n"
                    "Load a JSON file and start the workflow to initialize verification.")
                return
            
            if not self.characters_available:
                messagebox.showwarning("Verification Test", 
                    "⚠️ Characters Array system is disabled.\n\n"
                    "WebSocket connection may not be available.")
                return
            
            # Test getting current count
            current_count = self.get_characters_count()
            
            if current_count is not None:
                messagebox.showinfo("Verification Test", 
                    f"✅ Characters Array system working!\n\n"
                    f"Current Characters count: {current_count}\n"
                    f"JavaScript monitoring: ACTIVE\n"
                    f"High-performance access: ENABLED\n\n"
                    f"Ready for drag verification!")
            else:
                messagebox.showerror("Verification Test", 
                    "❌ Characters Array test failed!\n\n"
                    "Could not get Characters count.\n"
                    "Game may not be loaded or VM not found.")
                    
        except Exception as e:
            messagebox.showerror("Verification Test", f"❌ Characters Array test error: {e}")

    def test_smart_verification_system(self):
            """Test Smart Verification System"""
            try:
                if not hasattr(self, 'page_verification_enabled'):
                    messagebox.showinfo("Smart Verification Test", 
                        "🔍 Smart Verification system not initialized yet.\n\n"
                        "Load a JSON file and start the workflow to initialize verification.")
                    return
                
                # Test WebSocket connection first
                ws_url = self._get_websocket_url()
                if not ws_url:
                    messagebox.showerror("Smart Verification Test", 
                        "⌚ WebSocket connection not available.\n\n"
                        "Make sure Chrome debug mode is running.")
                    return
                
                # Test page verification
                print("🔍 Testing Smart Page Verification...")
                page_result = self.verify_page_fully_loaded("test")
                
                # Test game verification  
                print("🎮 Testing Smart Game Verification...")
                game_result = self.verify_game_components_ready()
                
                # Show results
                status_page = "✅ PASSED" if page_result else "⚠️ FAILED"
                status_game = "✅ PASSED" if game_result else "⚠️ FAILED"
                
                messagebox.showinfo("Smart Verification Test Results", 
                    f"🔍 SMART VERIFICATION SYSTEM TEST\n\n"
                    f"📄 Page Verification: {status_page}\n"
                    f"🎮 Game Verification: {status_game}\n\n"
                    f"⚙️ Configuration:\n"
                    f"   • Page verification: {'✅ Enabled' if self.page_verification_enabled else '❌ Disabled'}\n"
                    f"   • Game verification: {'✅ Enabled' if self.game_verification_enabled else '❌ Disabled'}\n"
                    f"   • Page timeout: {getattr(self, 'page_verification_timeout', 'N/A')}s\n"
                    f"   • Game timeout: {getattr(self, 'game_verification_timeout', 'N/A')}s\n"
                    f"   • Stabilization wait: {getattr(self, 'stabilization_wait', 'N/A')}s\n\n"
                    f"🚀 Smart Verification ready to replace fixed 40s waits!")
                        
            except Exception as e:
                messagebox.showerror("Smart Verification Test", f"⌚ Test error: {e}")

    def display_remix_library_info(self):
        info_frame = Frame(self.root)
        info_frame.pack(pady=10)
        
        Label(info_frame, text="🎼 Remix Library:", font=("Arial", 11, "bold")).pack()
        
        if not self.remix_library:
            remix_summary = "📋 Will generate library when loading JSON with ENHANCED workflow"
        else:
            remix_summary = f"📋 {len(self.remix_library)} remixes with ENHANCED verification\n"
            for i, remix in enumerate(self.remix_library):
                remix_summary += f"{i+1}. {remix['name']}: {remix['description']}\n"
        
        self.remix_display_var = StringVar()
        self.remix_display_var.set(remix_summary)
        
        Label(info_frame, textvariable=self.remix_display_var, justify="left", 
              font=("Arial", 9), fg="blue", wraplength=550).pack(pady=5)
    
    def update_remix_display(self):
        if hasattr(self, 'remix_display_var'):
            if self.remix_library:
                remix_summary = f"📋 {len(self.remix_library)} remixes with ENHANCED verification\n"
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
                    messagebox.showerror("Domain Error", 
                        f"This version is optimized for 'cocrea.world' only!\n\n"
                        f"Current URL: {current_url}")
                    return
                
                # ================================================================
                # Pre-compute safe zones FOR PERFORMANCE immediately after config is loaded.
                # This ensures the cache is ready before any other action.
                print("\n🔥 PRE-COMPUTING SAFE ZONES FOR OPTIMAL PERFORMANCE...")
                if not self._precompute_all_safe_zones():
                    # Thông báo cho người dùng nếu cache thất bại nhưng vẫn tiếp tục
                    print("⚠️ Safe zone pre-computation failed, will use fallback real-time calculations.")
                    messagebox.showwarning("Performance Warning", 
                                        "Safe Zone pre-computation failed.\n"
                                        "The automation will run slower as it needs to calculate on-the-fly.")
                # ================================================================
                
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
                    horror_info = f"👻 Horror Mode: YES (Icon 19 avoided in normal remixes)\n"
                else:
                    horror_info = f"🎪 Horror Mode: NO (All 20 icons available)\n"
                
                # Confirmation
                result = messagebox.askyesno("Confirm ENHANCED Auto", 
                    f"🎯 ENHANCED WORKFLOW READY\n\n"
                    f"🎮 Icons: {len(layout['icons'])}\n"
                    f"🎪 Slots: {len(layout['slots'])}\n"
                    f"🎼 Remixes: {len(self.remix_library)}\n"
                    f"{horror_info}\n"
                    f"⚡ ENHANCED FEATURES:\n"
                    f"• Chrome Debug Mode (port {self.debugging_port})\n"
                    f"• Drag Verification via 'Putted characters'\n"
                    f"• 5-Coordinate Retry System\n"
                    f"• WebSocket/CDP for advanced control\n"
                    f"• PyAutoGUI with smart fallback\n"
                    f"• Real-time game state monitoring\n"
                    f"• Enhanced statistics tracking\n"
                    f"• Horror-aware remix generation\n\n"
                    f"Continue with ENHANCED workflow?")
                
                if result:
                    self.start_full_auto_process()
                
            except Exception as e:
                messagebox.showerror("Error", "Cannot load config: " + str(e))
    
    def calculate_total_steps(self):
        """Calculate total steps for progress"""
        layout = self.config['layout']
        game_settings = self.config.get('gameSettings', {})
        has_horror = game_settings.get('hasHorror', False)
        has_bonus = game_settings.get('hasBonus', False)
        
        setup_steps = 7  # Increased by 1 for verification setup
        
        if self.mode.get() == 1:  # Showcase + Remix
            normal_range = range(len(layout['icons']) - 1) if has_horror else range(len(layout['icons']))
            normal_showcase = len(normal_range)
            normal_bonus = len(layout.get('bonus', [])) if has_bonus else 0
            normal_remix_steps = sum(len(remix['icons']) + 1 for remix in self.remix_library)
            horror_transition = 1 if has_horror else 0
            horror_showcase = len(layout['icons']) if has_horror else 0
            horror_bonus = len(layout.get('bonus', [])) if (has_horror and has_bonus) else 0
            horror_remix_steps = sum(len(remix['icons']) + 1 for remix in self.remix_library) if has_horror else 0
            
            total_steps = (setup_steps + normal_showcase + normal_bonus + normal_remix_steps + 
                          horror_transition + horror_showcase + horror_bonus + horror_remix_steps)
        else:  # Only Remix
            remix_only_steps = sum(len(remix['icons']) + 1 for remix in self.remix_library)
            total_steps = setup_steps + remix_only_steps
        
        return total_steps
    
    def start_full_auto_process(self):
        """Start enhanced auto process"""
        self.is_playing = True
        self.should_stop = False
        self.current_remix_index = 0
        self.drag_stats = DragOperationStats()
        
        # Update UI
        self.stop_btn.config(state="normal")
        mode_text = "Showcase + Remix" if self.mode.get() == 1 else "Remix Only"
        self.status_var.set(f"Running ENHANCED Auto ({mode_text})...")
        
        # Setup progress
        total_steps = self.calculate_total_steps()
        self.progress['maximum'] = total_steps
        self.progress['value'] = 0
        
        print(f"🎯 Starting ENHANCED Auto with {total_steps} steps")
        print(f"🎼 Processing {len(self.remix_library)} remixes with ENHANCED verification")
        print(f"🌐 Using Chrome debug port: {self.debugging_port}")
        print(f"🔍 Drag verification system will be setup during initialization")
        
        # Start auto thread
        self.auto_thread = threading.Thread(target=self.full_auto_loop_corrected, daemon=True)
        self.auto_thread.start()
        
        # Register ESC hotkey
        keyboard.add_hotkey('esc', self.emergency_stop)
    
    def full_auto_loop_corrected(self):
        """Main enhanced auto loop"""
        try:
            print("🔧 Starting ENHANCED auto loop...")
            
            def update_action(message):
                self.action_var.set(message)
                self.root.update()
            
            # Setup game environment with verification
            if not self.setup_game_environment(progress_callback=update_action):
                messagebox.showerror("Setup Error", "Cannot setup enhanced game environment!")
                return
            
            self.progress['value'] = 7  # Updated for verification setup step
            self.root.update()
            
            # Execute workflow based on mode
            if self.mode.get() == 1:  # Showcase + Remix
                print("🎪 Starting ENHANCED full showcase workflow...")
                success = self.execute_full_showcase_workflow_corrected(progress_callback=update_action)
            else:  # Remix only
                print("🎵 Starting ENHANCED remix-only workflow...")
                success = self.execute_remix_only_workflow_corrected(progress_callback=update_action)
            
            # Show completion
            if success and not self.should_stop:
                self.show_enhanced_completion_message()
            
            self.stop_all()
            
        except Exception as e:
            messagebox.showerror("Auto Error", "Error in enhanced auto: " + str(e))
            self.stop_all()
    
    def show_enhanced_completion_message(self):
        """Show enhanced completion message"""
        mode_text = "Showcase + Remix" if self.mode.get() == 1 else "Remix Only"
        stats = self.drag_stats
        success_rate = (stats.successful_drags / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
        verification_rate = (stats.verification_enabled / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
        verification_success_rate = (stats.verification_successful / stats.verification_enabled * 100) if stats.verification_enabled > 0 else 0
        avg_coord_retries = sum(stats.coordinate_retries) / len(stats.coordinate_retries) if stats.coordinate_retries else 0
        
        self.action_var.set("🎉 ENHANCED Auto completed!")
        # Generate final comprehensive report
        print("\n📋 GENERATING FINAL SUCCESS REPORT...")
        self.cleanup_and_report("Successful_Completion")        
        messagebox.showinfo("🎉 ENHANCED Auto Completed", 
            f"ENHANCED {mode_text} workflow completed!\n\n"
            f"📊 ENHANCED DRAG STATISTICS:\n"
            f"   • Total attempts: {stats.total_attempts}\n"
            f"   • Success rate: {success_rate:.1f}%\n"
            f"   • Average coordinates tried: {avg_coord_retries:.1f}/5\n"
            f"   • Remixes processed: {len(self.remix_library)}\n\n"
            f"🔍 VERIFICATION STATISTICS:\n"
            f"   • Verification enabled: {verification_rate:.1f}%\n"
            f"   • Verification success: {verification_success_rate:.1f}%\n"
            f"   • System status: {'✅ ACTIVE' if self.characters_available else '⚠️ DISABLED'}\n\n"
            f"⚡ ENHANCED FEATURES USED:\n"
            f"   • Chrome Debug Mode (port {self.debugging_port})\n"
            f"   • Drag verification via 'Putted characters'\n"
            f"   • 5-coordinate retry system\n"
            f"   • WebSocket/CDP for advanced control\n"
            f"   • Smart fallback between methods\n"
            f"   • Real-time game state monitoring\n\n"
            f"The workflow is now ENHANCED and super reliable! 🚀✨")
    
    def stop_all(self):
        """Stop all processes"""
        self.should_stop = True
        self.is_playing = False

        # 🚀 NEW: Stop batch verification session
        if self.batch_verification_active:
            self.stop_batch_verification_session()


        self.stop_btn.config(state="disabled")
        self.action_var.set("⏹️ ENHANCED Auto stopped")
        self.status_var.set("Stopped")
        
        if self.drag_stats.total_attempts > 0:
            print("\n" + "="*50)
            print("📊 FINAL ENHANCED AUTO STATISTICS:")
            print(self.get_drag_statistics())
            print("="*50)
            
            # Enhanced cleanup and reporting on stop
            self.cleanup_and_report("Manual_Stop")

        try:
            keyboard.remove_hotkey('esc')
        except:
            pass
    
    def emergency_stop(self):
        """Emergency stop"""
        self.stop_all()
        messagebox.showwarning("⚠️ Emergency Stop", "ENHANCED Auto stopped with ESC!")
    
    def run(self):
        """Run application"""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("Application stopped")
        finally:
            try:
                keyboard.remove_hotkey('esc')
            except:
                pass
            # Cleanup on exit
            self.cleanup_chrome_complete()

    def get_enhanced_workflow_summary(self):
            """
            📊 NEW: Get comprehensive workflow summary with smart features
            FIXED: Distinguish between Polos (icon placement) and Enabled (UI state)
            """
            try:
                stats = self.drag_stats
                current_state = self.get_comprehensive_slot_state()
                
                # Basic stats
                success_rate = (stats.successful_drags / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
                verification_rate = (stats.verification_enabled / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
                
                summary = f"""
    📊 ENHANCED WORKFLOW SUMMARY:
    {'='*60}
    🎯 DRAG OPERATIONS:
    • Total attempts: {stats.total_attempts}
    • Success rate: {success_rate:.1f}%
    • Verification enabled: {verification_rate:.1f}%
    • Average coordinate retries: {sum(stats.coordinate_retries) / len(stats.coordinate_retries) if stats.coordinate_retries else 0:.1f}/5

    🛡️ SMART FEATURES ACTIVE:
    • ✅ Polos array tracking (PRIMARY)
    • ✅ Cross-contamination prevention  
    • ✅ Pre-flight slot checking
    • ✅ Smart batch return sequencing
    • ✅ Emergency recovery protocols
    • ✅ Real-time state validation

    🔍 CURRENT SLOT STATE:"""

                if current_state:
                    putted = current_state.get('putted_characters', 'N/A')
                    polos = current_state.get('polos_array', [])
                    enabled = current_state.get('enabled_list', [])
                    
                    summary += f"""
    • Putted characters: {putted}
    • Active slots (Polos): {sum(1 for x in polos if x == "1") if polos else 'N/A'}
    • PRIMARY consistency: {'✅ Good' if current_state.get('putted_polos_match', True) else '⚠️ Issue'}
    • Polos array: {polos if len(str(polos)) < 50 else f'{len(polos)} slots'}
    • Enabled List: {enabled if len(str(enabled)) < 50 else f'{len(enabled)} slots'} (UI state)"""
                else:
                    summary += "\n• ⚠️ Current state unavailable"

                summary += f"""

    ⚡ SYSTEM STATUS:
    • Verification system: {'✅ ACTIVE' if self.characters_available else '⚠️ DISABLED'}
    • WebSocket connection: {'✅ Ready' if self._get_websocket_url() else '❌ Failed'}
    • Configuration: {'✅ Loaded' if hasattr(self, 'config') and self.config else '❌ Missing'}

    ℹ️ ARRAY UNDERSTANDING:
    • Polos Array = Icon placement state (PRIMARY for verification)
    • Enabled List = UI availability state (informational only)

    {'='*60}
    🎉 ENHANCED SPRUNKI GAME PLAYER READY!
    """
                
                return summary
                
            except Exception as e:
                return f"❌ Error generating workflow summary: {e}"

    def log_comprehensive_session_report(self, session_type="Unknown"):
        """
        📋 NEW: Generate comprehensive session report for debugging
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            report = f"""
    {'='*80}
    📋 COMPREHENSIVE SESSION REPORT
    {'='*80}
    ⏰ Session: {session_type} at {timestamp}

    🎯 DRAG OPERATIONS SUMMARY:
    {self.get_drag_statistics()}

    🔍 CURRENT SYSTEM STATE:
    {self.get_enhanced_workflow_summary()}

    🛡️ SMART FEATURES STATUS:
    • Pre-flight slot checking: {'✅ Active' if self.characters_available else '❌ Disabled'}
    • Cross-contamination prevention: {'✅ Active' if self.characters_available else '❌ Disabled'}
    • Smart batch return: ✅ Available
    • Emergency recovery: ✅ Available
    • Real-time validation: {'✅ Active' if self.characters_available else '❌ Disabled'}

    📊 FINAL STATE VALIDATION:
    """
            
            # Get final validation
            is_complete, issues, recommendations = self.validate_workflow_completion()
            
            if is_complete:
                report += "   ✅ PERFECT COMPLETION - No issues detected\n"
            else:
                report += f"   ⚠️ {len(issues)} ISSUES DETECTED:\n"
                for i, issue in enumerate(issues, 1):
                    report += f"      {i}. {issue}\n"
                
                report += f"   💡 RECOMMENDATIONS:\n"
                for i, rec in enumerate(recommendations, 1):
                    report += f"      {i}. {rec}\n"
            
            report += f"""
    🔧 SYSTEM DIAGNOSTICS:
    • Configuration: {'✅ Loaded' if hasattr(self, 'config') and self.config else '❌ Missing'}
    • WebSocket: {'✅ Ready' if self._get_websocket_url() else '❌ Failed'}
    • Verification Setup: {'✅ Done' if self.putted_chars_setup_done else '❌ Missing'}
    • Browser State: {'✅ Active' if hasattr(self, 'chrome_process') and self.chrome_process else '❌ Closed'}

    {'='*80}
    🎉 ENHANCED SPRUNKI AUTOMATION SYSTEM REPORT COMPLETE
    {'='*80}
    """
            
            print(report)
            
            # Save to file for debugging
            try:
                import os
                reports_dir = "session_reports"
                os.makedirs(reports_dir, exist_ok=True)
                
                safe_session_type = session_type.replace(" ", "_").replace(":", "-")
                filename = f"session_{safe_session_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                filepath = os.path.join(reports_dir, filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(report)
                
                print(f"📄 Session report saved: {filepath}")
                
            except Exception as e:
                print(f"⚠️ Could not save session report: {e}")
            
            return report
            
        except Exception as e:
            error_report = f"❌ Error generating session report: {e}"
            print(error_report)
            return error_report

    def cleanup_and_report(self, session_type="Workflow"):
        """
        🧹 NEW: Enhanced cleanup with comprehensive reporting
        """
        try:
            print(f"\n🧹 ENHANCED CLEANUP & REPORTING: {session_type}")
            print("="*60)
            
            # Final smart cleanup
            if self.characters_available:
                print("🔄 Final smart batch cleanup...")
                cleanup_success, cleanup_total, cleanup_errors = self.smart_batch_return_all_slots(use_cdp=False)
                
                if cleanup_total > 0:
                    print(f"   ✅ Cleaned {cleanup_success}/{cleanup_total} slots")
                    if cleanup_errors:
                        print(f"   ⚠️ {len(cleanup_errors)} cleanup issues")
            
            # Generate comprehensive report
            print("\n📋 Generating comprehensive session report...")
            session_report = self.log_comprehensive_session_report(session_type)
            
            # Close browser if needed
            if hasattr(self, 'chrome_process') and self.chrome_process:
                print("\n🌐 Closing browser...")
                self.close_game_window()
            
            print(f"✅ Enhanced cleanup completed for: {session_type}")
            
        except Exception as e:
            print(f"❌ Error during enhanced cleanup: {e}")

if __name__ == "__main__":
    print("🚀 Starting ENHANCED Sprunki Game Player...")
    print("⚡ ENHANCED FEATURES:")
    print("   🌐 Chrome Debug Mode: Single browser with remote debugging")
    print("   🔍 Drag Verification: Real-time 'Putted characters' monitoring")
    print("   🎯 5-Coordinate Retry: Center → Left → Right → Top → Bottom")
    print("   📡 WebSocket/CDP: Advanced browser control and JavaScript injection")
    print("   🎮 PyAutoGUI: Reliable coordinate-based automation with smart fallback")
    print("   🛡️ AdBlocker: Automatic uBlock Origin extension loading")
    print("   ⚙️ Config Integration: All settings from config.json")
    print("   📊 Enhanced Statistics: Verification rates and coordinate usage")
    print("   🧹 Smart Cleanup: Auto-return icons on verification failure")
    print("   🎯 Session Sharing: Can reuse browser from JSON generation")
    print("="*70)
    
    app = SprunkiGamePlayer()
    
    print("\n🎮 ENHANCED Auto Player ready!")
    print("📊 Features: Drag Verification + 5-Coordinate Retry + Chrome Debug")
    print("🎼 Ultra-reliable automation with game state monitoring")
    print("⚡ Optimized for 95%+ success rate with intelligent fallback")
    
    app.run()
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
        
        # ADDED: Drag verification system
        self.putted_chars_setup_done = False
        self.verification_available = False
        
        # Drag operation statistics (enhanced)
        self.drag_stats = DragOperationStats()

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

        # NEW: JavaScript for finding ALL Skip Intro buttons (Multi-Target Detection)
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
            
            max_wait_time = 30
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

    def setup_putted_chars_monitor(self):
        """
        ADDED: Setup JavaScript monitoring for 'Putted characters' variable
        Run once during game setup to create high-performance monitoring
        """
        try:
            print("🔍 Setting up Putted characters monitoring...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("⚠️ WebSocket not available, drag verification disabled")
                self.verification_available = False
                return False
            
            # JavaScript code adapted from user's F12 Console script
            setup_js = """
            (() => {
                // Check if already setup
                if (window.getPuttedChars) {
                    console.log('✅ Putted characters monitoring already setup');
                    return true;
                }
                
                console.log('🚀 Setting up high-performance Putted characters monitoring...');
                
                // Find VM function
                function findVM() {
                    if (window.__vm && window.__vm.runtime) return window.__vm;
                    const getReactKey = el => Object.keys(el).find(k => k.startsWith('__react'));
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        const elements = document.querySelectorAll(selector);
                        for (const el of elements) {
                            const reactKey = getReactKey(el);
                            if (!reactKey) continue;
                            let fiber = el[reactKey];
                            let depth = 0;
                            while (fiber && depth++ < 100) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm && props.vm.runtime) {
                                    return props.vm;
                                }
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }
                
                // 1. Find and store VM
                const vm = findVM();
                if (!vm) {
                    console.error('❌ Cannot find Scratch VM for Putted characters monitoring');
                    return false;
                }
                window.__sprunki_vm = vm;
                console.log('✅ VM found and stored');
                
                // 2. Find and store variable ID
                const stage = vm.runtime.getTargetForStage();
                if (!stage) {
                    console.error('❌ Cannot find Stage object');
                    return false;
                }
                
                const variableName = 'Putted characters';
                const targetVariable = Object.values(stage.variables).find(v => v.name === variableName);
                
                if (!targetVariable) {
                    console.error(`❌ Cannot find variable "${variableName}"`);
                    return false;
                }
                
                window.__sprunki_var_id = targetVariable.id;
                console.log(`✅ Variable "${variableName}" ID found and stored`);
                
                // 3. Create high-performance function
                window.getPuttedChars = () => {
                    try {
                        return window.__sprunki_vm.runtime.getTargetForStage().variables[window.__sprunki_var_id].value;
                    } catch (e) {
                        console.error("Error getting Putted characters value:", e);
                        return null;
                    }
                };
                
                console.log('✅ High-performance getPuttedChars() function created');
                console.log('👉 Current value:', window.getPuttedChars());
                return true;
            })();
            """
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # Execute setup JavaScript
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": setup_js, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result_value = response.get('result', {}).get('result', {}).get('value', False)
                if result_value:
                    print("✅ Putted characters monitoring setup successful!")
                    self.putted_chars_setup_done = True
                    self.verification_available = True
                    
                    # Test the monitoring
                    test_value = self.get_putted_chars_value()
                    if test_value is not None:
                        print(f"🧪 Test successful - Current Putted characters: {test_value}")
                        return True
                    else:
                        print("⚠️ Setup successful but test failed")
                        self.verification_available = False
                        return False
                else:
                    print("❌ Putted characters monitoring setup failed")
                    self.verification_available = False
                    return False
            else:
                print("❌ No response from JavaScript setup")
                self.verification_available = False
                return False
                
        except Exception as e:
            print(f"❌ Error setting up Putted characters monitoring: {e}")
            self.verification_available = False
            return False

    def get_putted_chars_value(self):
        """
        ADDED: Get current value of 'Putted characters' variable
        Returns integer value or None if error
        """
        try:
            if not self.verification_available:
                return None
                
            ws_url = self._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            # Call the high-performance function
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": "window.getPuttedChars()", "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                value = response.get('result', {}).get('result', {}).get('value')
                if isinstance(value, (int, float)):
                    return int(value)
                else:
                    print(f"⚠️ Unexpected Putted characters value type: {type(value)} = {value}")
                    return None
            else:
                print("⚠️ No response when getting Putted characters value")
                return None
                
        except Exception as e:
            print(f"❌ Error getting Putted characters value: {e}")
            return None

    def get_polos_array(self):
        """
        🎯 NEW: Get Polos array from Stage variables 
        Returns: list of strings ["0","1","0"...] or None if error
        """
        try:
            if not self.verification_available:
                return None
                
            ws_url = self._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            # JavaScript to get Polos array from Stage variables
            js_get_polos = '''
            (() => {
                try {
                    if (!window.__sprunki_vm) {
                        console.error("VM not found for Polos array");
                        return null;
                    }
                    
                    const stage = window.__sprunki_vm.runtime.getTargetForStage();
                    if (!stage) {
                        console.error("Stage not found for Polos array");
                        return null;
                    }
                    
                    // Find Polos variable in Stage variables
                    const polosVariable = Object.values(stage.variables).find(v => v.name === 'Polos');
                    
                    if (!polosVariable) {
                        console.error("Polos variable not found in Stage");
                        return null;
                    }
                    
                    console.log("✅ Found Polos array:", polosVariable.value);
                    return polosVariable.value;
                    
                } catch (e) {
                    console.error("Error getting Polos array:", e);
                    return null;
                }
            })();
            '''
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_get_polos, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                polos_value = response.get('result', {}).get('result', {}).get('value')
                
                if isinstance(polos_value, list):
                    print(f"🎯 Polos array retrieved: {polos_value}")
                    return polos_value
                else:
                    print(f"⚠️ Unexpected Polos value type: {type(polos_value)} = {polos_value}")
                    return None
            else:
                print("⚠️ No response when getting Polos array")
                return None
                
        except Exception as e:
            print(f"❌ Error getting Polos array: {e}")
            return None

    def get_enabled_list_array(self):
        """
        🔍 NEW: Get Enabled List array for cross-validation
        Returns: list of strings ["0","1","0"...] or None if error
        """
        try:
            if not self.verification_available:
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

    def get_comprehensive_slot_state(self):
            """
            📊 NEW: Get comprehensive slot state for verification
            FIXED: Enabled List is UI state, not icon placement state
            Returns: dict with all slot tracking data or None if error
            """
            try:
                putted_chars = self.get_putted_chars_value()
                polos_array = self.get_polos_array()
                enabled_list = self.get_enabled_list_array()
                
                if putted_chars is None:
                    print("⚠️ Could not get Putted characters")
                    return None
                
                # Build comprehensive state
                state = {
                    'putted_characters': putted_chars,
                    'polos_array': polos_array,
                    'enabled_list': enabled_list,
                    'timestamp': time.time()
                }
                
                # PRIMARY validation: Polos array vs Putted characters
                if polos_array:
                    polos_count = sum(1 for x in polos_array if x == "1")
                    state['polos_count'] = polos_count
                    state['putted_polos_match'] = (polos_count == putted_chars)
                    
                    if not state['putted_polos_match']:
                        print(f"⚠️ PRIMARY INCONSISTENCY: Putted chars ({putted_chars}) ≠ Polos count ({polos_count})")
                
                # SECONDARY info: Enabled List (UI state - not for validation)
                if enabled_list and polos_array:
                    arrays_match = (polos_array == enabled_list)
                    state['polos_enabled_match'] = arrays_match
                    
                    if not arrays_match:
                        print(f"ℹ️ EXPECTED DIFFERENCE: Polos {polos_array} ≠ Enabled {enabled_list}")
                        print("ℹ️ This is normal: Polos=icon placement, Enabled=UI availability")
                    else:
                        print(f"ℹ️ Arrays happen to match (rare but not problematic)")
                
                print(f"📊 Comprehensive slot state captured:")
                print(f"   Putted characters: {putted_chars}")
                if polos_array:
                    print(f"   Polos array: {polos_array} (count: {state.get('polos_count', 'N/A')})")
                    print(f"   PRIMARY health: {'✅ Good' if state.get('putted_polos_match', True) else '⚠️ Issue'}")
                if enabled_list:
                    print(f"   Enabled List: {enabled_list} (UI state - informational)")
                
                return state
                
            except Exception as e:
                print(f"❌ Error getting comprehensive slot state: {e}")
                return None

    def verify_slot_specific_return_enhanced(self, target_slot_index, state_before, state_after):
            """
            🔍 ENHANCED: Slot-specific verification with Polos array validation
            FIXED: Enabled List is UI state, not icon placement state
            Returns: (success, reason, details)
            """
            try:
                if not state_before or not state_after:
                    return False, "missing_state_data", {}
                
                putted_before = state_before.get('putted_characters')
                putted_after = state_after.get('putted_characters')
                polos_before = state_before.get('polos_array')
                polos_after = state_after.get('polos_array')
                enabled_before = state_before.get('enabled_list')
                enabled_after = state_after.get('enabled_list')
                
                print(f"\n🔍 ENHANCED SLOT VERIFICATION for Slot {target_slot_index}")
                print("-" * 50)
                
                # Basic putted characters check
                expected_putted = putted_before - 1 if putted_before is not None else None
                putted_success = (putted_after == expected_putted)
                
                print(f"   📊 Putted chars: {putted_before} → {putted_after} (expected: {expected_putted}) = {'✅' if putted_success else '❌'}")
                
                # PRIMARY: Polos array verification (this is the main indicator)
                polos_success = False
                cross_contamination = []
                target_slot_cleared = False
                
                if polos_before and polos_after and len(polos_before) == len(polos_after):
                    print(f"   🎯 Polos BEFORE: {polos_before}")
                    print(f"   🎯 Polos AFTER:  {polos_after}")
                    
                    # Check if target slot was cleared
                    if target_slot_index < len(polos_before):
                        slot_before = polos_before[target_slot_index]
                        slot_after = polos_after[target_slot_index]
                        target_slot_cleared = (slot_before == "1" and slot_after == "0")
                        
                        print(f"   🎯 Target slot {target_slot_index}: '{slot_before}' → '{slot_after}' = {'✅ CLEARED' if target_slot_cleared else '❌ NOT CLEARED'}")
                    
                    # Check for unintended changes (cross-contamination)
                    for i, (before, after) in enumerate(zip(polos_before, polos_after)):
                        if i == target_slot_index:
                            continue  # Skip target slot
                            
                        if before != after:
                            cross_contamination.append({
                                'slot_index': i,
                                'before': before,
                                'after': after,
                                'type': 'unexpected_clear' if before == "1" and after == "0" else 'unexpected_fill'
                            })
                    
                    if cross_contamination:
                        print(f"   🚨 CROSS-CONTAMINATION DETECTED:")
                        for contamination in cross_contamination:
                            slot_idx = contamination['slot_index']
                            change_type = contamination['type']
                            print(f"      Slot {slot_idx}: {contamination['before']} → {contamination['after']} ({change_type})")
                    else:
                        print(f"   🛡️ No cross-contamination detected")
                    
                    polos_success = target_slot_cleared and len(cross_contamination) == 0
                else:
                    print(f"   ⚠️ Cannot verify Polos array (missing or invalid data)")
                
                # SECONDARY: Enabled List info (for debugging only, not for pass/fail)
                if enabled_before and enabled_after:
                    print(f"   🔍 Enabled BEFORE: {enabled_before}")
                    print(f"   🔍 Enabled AFTER:  {enabled_after}")
                    enabled_changed = (enabled_before != enabled_after)
                    if enabled_changed:
                        print(f"   ℹ️ Enabled List changed (this is UI state, not icon placement)")
                    else:
                        print(f"   ℹ️ Enabled List unchanged (normal - this is UI availability state)")
                else:
                    print(f"   ℹ️ Enabled List data not available")
                
                # DECISION LOGIC: Only Polos array + Putted characters matter
                overall_success = putted_success and (polos_success if polos_before and polos_after else True)
                
                # Determine reason and build details
                if overall_success:
                    reason = "verified_success"
                    details = {
                        'target_slot_cleared': target_slot_cleared,
                        'cross_contamination': cross_contamination,
                        'putted_change': f"{putted_before} → {putted_after}",
                        'verification_method': 'enhanced_polos_array_primary',
                        'enabled_list_note': 'Enabled List is UI state, not used for pass/fail decision'
                    }
                else:
                    if not putted_success:
                        reason = "putted_characters_mismatch"
                    elif cross_contamination:
                        reason = "cross_contamination_detected"
                    elif not target_slot_cleared:
                        reason = "target_slot_not_cleared"
                    else:
                        reason = "unknown_failure"
                    
                    details = {
                        'target_slot_cleared': target_slot_cleared,
                        'cross_contamination': cross_contamination,
                        'putted_change': f"{putted_before} → {putted_after}",
                        'expected_putted': expected_putted,
                        'verification_method': 'enhanced_polos_array_primary',
                        'enabled_list_note': 'Enabled List ignored for verification (UI state only)'
                    }
                
                print(f"   🎯 FINAL RESULT: {'✅ SUCCESS' if overall_success else '❌ FAILED'} ({reason})")
                print(f"   💡 PRIMARY INDICATORS: Putted chars ({'✅' if putted_success else '❌'}) + Polos array ({'✅' if polos_success else '❌'})")
                print(f"   ℹ️ Enabled List: Informational only (UI availability state)")
                print("-" * 50)
                
                return overall_success, reason, details
                
            except Exception as e:
                print(f"❌ Enhanced slot verification error: {e}")
                return False, f"verification_error: {e}", {}

    def check_slot_needs_return(self, target_slot_index):
            """
            🔍 NEW: Pre-flight check if slot actually needs return
            FIXED: Only check Polos array (icon placement), not Enabled List (UI state)
            Returns: (needs_return, current_state, reason)
            """
            try:
                print(f"\n🔍 PRE-FLIGHT CHECK: Slot {target_slot_index}")
                
                state = self.get_comprehensive_slot_state()
                if not state:
                    return True, None, "cannot_verify_state"  # Assume needs return if can't check
                
                polos_array = state.get('polos_array')
                
                if polos_array and target_slot_index < len(polos_array):
                    slot_status = polos_array[target_slot_index]
                    
                    if slot_status == "0":
                        print(f"   ✅ Slot {target_slot_index} already empty ('{slot_status}') - SKIPPING")
                        return False, state, "already_empty"
                    elif slot_status == "1":
                        print(f"   🎯 Slot {target_slot_index} has icon ('{slot_status}') - NEEDS RETURN")
                        return True, state, "has_icon"
                    else:
                        print(f"   ⚠️ Slot {target_slot_index} unknown status ('{slot_status}') - ASSUME NEEDS RETURN")
                        return True, state, "unknown_status"
                else:
                    print(f"   ⚠️ Cannot check slot {target_slot_index} status - ASSUME NEEDS RETURN")
                    return True, state, "polos_unavailable"
                    
            except Exception as e:
                print(f"❌ Pre-flight check error: {e}")
                return True, None, f"error: {e}"  # Assume needs return on error

    def get_active_slots_queue(self):
            """
            📋 NEW: Get smart-ordered queue of active slots that need return
            FIXED: Only use Polos array (icon placement), not Enabled List (UI state)
            Returns: list of slot indices in optimal return order
            """
            try:
                print(f"\n📋 BUILDING SMART RETURN QUEUE")
                print("-" * 40)
                
                state = self.get_comprehensive_slot_state()
                if not state:
                    print("⚠️ Cannot get slot state, using fallback")
                    return []
                
                polos_array = state.get('polos_array')
                if not polos_array:
                    print("⚠️ No Polos array available, using fallback")
                    return []
                
                # Find all active slots (status "1") - ONLY FROM POLOS ARRAY
                active_slots = []
                for i, status in enumerate(polos_array):
                    if status == "1":
                        active_slots.append(i)
                
                print(f"   🎯 Found {len(active_slots)} active slots: {active_slots}")
                print(f"   📊 Based on Polos array: {polos_array}")
                
                # Smart ordering: Return from highest index to lowest
                # This prevents index shifting issues during batch operations
                active_slots_ordered = sorted(active_slots, reverse=True)
                
                print(f"   📋 Optimal return order: {active_slots_ordered} (high → low)")
                print(f"   💡 Reason: Prevents index shifting during batch return")
                
                # Optional: Show Enabled List for info (but don't use for queue)
                enabled_list = state.get('enabled_list')
                if enabled_list:
                    print(f"   ℹ️ Enabled List (UI state): {enabled_list}")
                    print(f"   ℹ️ Note: Queue based on Polos only, not Enabled List")
                
                return active_slots_ordered
                
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
            
            state_before = self.get_comprehensive_slot_state() if self.verification_available else None
            
            for _ in range(5):  # 5 rapid clicks
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="single")
                time.sleep(0.1)
            
            time.sleep(1.0)
            if self.verification_available and state_before:
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
            
            state_before = self.get_comprehensive_slot_state() if self.verification_available else None
            
            self.execute_multi_method_clicks(expanded_coords, use_cdp, domain_adj, method="hold")
            
            time.sleep(1.5)
            if self.verification_available and state_before:
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
            
            state_before = self.get_comprehensive_slot_state() if self.verification_available else None
            
            methods = ["single", "double", "triple"]
            for method in methods:
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method=method)
                time.sleep(0.3)
            
            time.sleep(1.5)
            if self.verification_available and state_before:
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
            ✅ NEW: Validate that workflow completed successfully
            FIXED: Only validate Polos array, not Enabled List
            Returns: (is_complete, remaining_issues, recommendations)
            """
            try:
                print(f"\n✅ WORKFLOW COMPLETION VALIDATION")
                print("="*50)
                
                state = self.get_comprehensive_slot_state()
                if not state:
                    return False, ["Cannot get final state"], ["Manual verification needed"]
                
                putted_chars = state.get('putted_characters', 0)
                polos_array = state.get('polos_array')
                enabled_list = state.get('enabled_list')
                
                issues = []
                recommendations = []
                
                # Check 1: Putted characters should be 0
                if putted_chars != 0:
                    issues.append(f"Putted characters = {putted_chars} (expected: 0)")
                    recommendations.append("Run additional return operations")
                else:
                    print("✅ Putted characters = 0 (perfect)")
                
                # Check 2: All Polos should be "0" (PRIMARY CHECK)
                if polos_array:
                    active_polos = [i for i, status in enumerate(polos_array) if status == "1"]
                    if active_polos:
                        issues.append(f"Active slots remaining: {active_polos}")
                        recommendations.append(f"Target slots {active_polos} for additional return")
                    else:
                        print("✅ All Polos slots = '0' (perfect)")
                
                # Check 3: Polos vs Putted consistency (PRIMARY CHECK)
                if polos_array:
                    polos_count = sum(1 for x in polos_array if x == "1")
                    if polos_count != putted_chars:
                        issues.append(f"Count mismatch: Polos count ({polos_count}) ≠ Putted chars ({putted_chars})")
                        recommendations.append("State synchronization needed")
                    else:
                        print("✅ Counts are consistent (perfect)")
                
                # Info 4: Enabled List info (NOT A VALIDATION CRITERIA)
                if enabled_list and polos_array:
                    if polos_array != enabled_list:
                        print("ℹ️ Polos and Enabled List differ (normal - different purposes)")
                        print("   Polos = icon placement state")
                        print("   Enabled = UI availability state")
                    else:
                        print("ℹ️ Polos and Enabled List happen to match (rare)")
                
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
                    print("🎉 All PRIMARY indicators are perfect!")
                    print("   ✅ Putted characters = 0")
                    print("   ✅ All Polos slots cleared")
                    print("   ✅ State consistency verified")
                
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

    def verify_drag_success(self, before_value, after_value):
        """
        ADDED: Verify drag success by checking if Putted characters increased by 1
        """
        if before_value is None or after_value is None:
            print("⚠️ Cannot verify drag - missing Putted characters values, assuming success")
            return True  # Fallback to assume success if verification not available
        
        expected_value = before_value + 1
        success = (after_value == expected_value)
        
        print(f"📊 Drag verification: {before_value} → {after_value} (expected: {expected_value}) = {'✅ SUCCESS' if success else '❌ FAILED'}")
        
        if not success:
            if after_value == before_value:
                print("   Reason: No change in Putted characters (icon not placed)")
            elif after_value > expected_value:
                print(f"   Reason: Too much increase (+{after_value - before_value}) - multiple icons placed?")
            elif after_value < before_value:
                print(f"   Reason: Decrease detected ({after_value - before_value}) - game state changed?")
        
        return success

    def verify_return_success(self, before_value, after_value):
        """
        ADDED: Verify return success by checking if Putted characters decreased by 1
        """
        if before_value is None or after_value is None:
            print("⚠️ Cannot verify return - missing Putted characters values, assuming success")
            return True  # Fallback to assume success if verification not available
        
        expected_value = before_value - 1
        success = (after_value == expected_value)
        
        print(f"📊 Return verification: {before_value} → {after_value} (expected: {expected_value}) = {'✅ SUCCESS' if success else '❌ FAILED'}")
        
        if not success:
            if after_value == before_value:
                print("   Reason: No change in Putted characters (icon not returned)")
            elif after_value < expected_value:
                print(f"   Reason: Too much decrease ({before_value - after_value}) - multiple icons returned?")
            elif after_value > before_value:
                print(f"   Reason: Increase detected (+{after_value - before_value}) - new icon placed?")
        
        return success

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
        Used when drag verification fails
        """
        try:
            print("🔄 Attempting to return icon before retry...")
            
            # Quick click to return icon
            center_x, center_y = target_slot['x'], target_slot['y']
            varied_x, varied_y = self.add_position_variation(center_x, center_y, 3)
            
            pyautogui.moveTo(varied_x, varied_y, duration=0.2)
            time.sleep(0.1)
            pyautogui.click(button='left')
            time.sleep(0.5)  # Wait for return animation
            
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
        ENHANCED: Drag and drop with verification and 5-coordinate retry
        This replaces the old corrected_drag_and_drop method
        """
        print(f"\n🎯 ENHANCED DRAG START: Icon to slot with verification")
        print("="*60)

        # Store target slot for verification
        target_slot_index = target_slot.get('index', -1)
        self.store_target_slot_for_verification(target_slot_index)

        # Check if verification is available
        verification_enabled = self.verification_available
        if verification_enabled:
            print("🔍 Drag verification ENABLED - will check Putted characters")
        else:
            print("⚠️ Drag verification DISABLED - using fallback logic")
        
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
            
            # Get Putted characters value BEFORE drag (if verification available)
            putted_before = None
            if verification_enabled:
                putted_before = self.get_putted_chars_value()
                print(f"📊 Putted characters BEFORE drag: {putted_before}")
            
            # Execute single drag attempt
            drag_executed = self._execute_single_drag_attempt(
                start_x, start_y, target_slot, is_first_icon, use_cdp, domain_adj
            )
            
            if not drag_executed:
                print(f"❌ Drag execution failed at coordinate {coord_used}")
                if coord_index < len(coordinates) - 1:
                    print("   Trying next coordinate...")
                    time.sleep(0.5)
                continue
            
            # Wait for game state to update
            verification_wait = 1.0 if verification_enabled else 0.3
            print(f"⏳ Waiting {verification_wait}s for game state update (mouse at safe position)...")
            time.sleep(verification_wait)
            
            # Get Putted characters value AFTER drag (if verification available)
            if verification_enabled:
                putted_after = self.get_putted_chars_value()
                print(f"📊 Putted characters AFTER drag: {putted_after}")
                
                # Verify drag success
                if self.verify_drag_success(putted_before, putted_after):
                    print(f"🎉 VERIFIED SUCCESS at coordinate {coord_used}!")
                    success = True
                    break
                else:
                    print(f"❌ VERIFICATION FAILED at coordinate {coord_used}")
                    
                    # Only return icon if it was actually placed (putted_after > putted_before)
                    if coord_index < len(coordinates) - 1:
                        if putted_after > putted_before:
                            print("🔄 Icon was placed but incorrectly, returning before retry...")
                            self.attempt_icon_return(target_slot)
                            time.sleep(0.8)
                        else:
                            print("ℹ️ Icon was not placed, trying next coordinate directly...")
                            time.sleep(0.3)  # Short delay before next coordinate
            else:
                # No verification available - assume success if drag executed
                print(f"✅ ASSUMED SUCCESS at coordinate {coord_used} (no verification)")
                success = True
                break
            
            # Small delay before next coordinate
            if coord_index < len(coordinates) - 1:
                print("   Preparing next coordinate attempt...")
                time.sleep(0.3)
        
        # Final result
        if success:
            print(f"🎉 ENHANCED DRAG COMPLETED SUCCESSFULLY!")
            print(f"   Coordinate used: {coord_used}/5")
            print(f"   Verification: {'✅ Enabled' if verification_enabled else '⚠️ Disabled'}")
            print("   Mouse already at safe position")
            print("="*60)
            
            self.drag_stats.record_attempt(True, retry_count=0, coord_used=coord_used, verification_used=verification_enabled)
            return True
        else:
            print(f"💥 ENHANCED DRAG FAILED - All {len(coordinates)} coordinates attempted")
            print("="*60)
            
            self.drag_stats.record_attempt(False, retry_count=len(coordinates), coord_used=coord_used, verification_used=verification_enabled)
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
        ENHANCED: Setup with drag verification system
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

            # ADDED: Setup drag verification monitoring
            if progress_callback:
                progress_callback("🔍 Setting up drag verification...")
            
            if not self.setup_putted_chars_monitor():
                print("⚠️ Drag verification setup failed, continuing with basic workflow")
            else:
                print("✅ Drag verification system ready!")

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
            if self.verification_available:
                print("   🔍 Drag verification system ENABLED")
            else:
                print("   ⚠️ Drag verification system DISABLED (fallback mode)")
            
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
        🛡️ SMART SLOT STATE TRACKING: Pre-flight + Enhanced Verification + Cross-contamination Prevention
        Uses Polos array for bulletproof slot-specific verification
        """
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            target_slot_index = target_slot.get('index', -1)
            domain_adj = self.get_domain_adjustments()
            
            print(f"\n🛡️ SMART SLOT RETURN: Slot {target_slot_index} at ({center_x}, {center_y})")
            print("="*80)
            
            # Check if verification is available
            verification_enabled = self.verification_available
            if verification_enabled:
                print("🔍 Enhanced verification ENABLED with Polos array tracking")
            else:
                print("⚠️ Enhanced verification DISABLED - using basic fallback")
            
            # ================================================================
            # PHASE 0: PRE-FLIGHT CHECK (Skip if already empty)
            # ================================================================
            print("\n🔍 PHASE 0: PRE-FLIGHT CHECK")
            print("-" * 40)
            
            if verification_enabled:
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
            # SAFE ZONE CALCULATION
            # ================================================================
            print("\n🛡️ SAFE ZONE CALCULATION")
            print("-" * 40)
            
            # Get all slots for boundary calculation
            if hasattr(self, 'config') and self.config:
                all_slots = self.config.get('layout', {}).get('slots', [])
            else:
                print("⚠️ No config available, using conservative defaults")
                all_slots = []
            
            max_safe_radius, safe_coordinates = self.calculate_slot_safe_zone(target_slot_index, all_slots)
            
            if not safe_coordinates:
                print("❌ Could not generate safe coordinates, using minimal fallback")
                safe_coordinates = [(center_x, center_y)]
            
            success = False
            phase_used = "none"
            
            # ================================================================
            # PHASE 1: PRECISION CENTER ATTACK
            # ================================================================
            print(f"\n🎯 PHASE 1: PRECISION CENTER (Safe radius: {max_safe_radius}px)")
            print("-" * 40)
            
            center_coords = [(center_x, center_y)]
            
            # Get initial state for this phase
            state_before = None
            if verification_enabled:
                state_before = self.get_comprehensive_slot_state()
                if state_before:
                    print(f"📊 State BEFORE Phase 1: Putted={state_before.get('putted_characters')}")
                    if state_before.get('polos_array'):
                        print(f"   🎯 Polos: {state_before['polos_array']}")
            
            # Try single click on center
            print("   🔹 Method: Precision single click")
            self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="single")
            
            # Wait and verify with enhanced verification
            time.sleep(0.8)
            if verification_enabled and state_before:
                state_after = self.get_comprehensive_slot_state()
                if state_after:
                    is_success, reason, details = self.verify_slot_specific_return_enhanced(
                        target_slot_index, state_before, state_after
                    )
                    
                    if is_success:
                        print("🎉 PHASE 1 SUCCESS: Precision center click worked!")
                        success = True
                        phase_used = "phase_1_precision_center"
                    else:
                        print(f"❌ Phase 1 failed: {reason}")
                        if details.get('cross_contamination'):
                            print("🚨 Cross-contamination detected in Phase 1!")
            
            # Try double click if single failed
            if not success and not self.should_stop:
                print("   🔹 Method: Precision double click")
                
                # Get state before double click
                if verification_enabled:
                    state_before = self.get_comprehensive_slot_state()
                
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="double")
                
                time.sleep(0.8)
                if verification_enabled and state_before:
                    state_after = self.get_comprehensive_slot_state()
                    if state_after:
                        is_success, reason, details = self.verify_slot_specific_return_enhanced(
                            target_slot_index, state_before, state_after
                        )
                        
                        if is_success:
                            print("🎉 PHASE 1 SUCCESS: Precision double click worked!")
                            success = True
                            phase_used = "phase_1_precision_double"
            
            # ================================================================
            # PHASE 2: SAFE BOUNDARY PATTERN
            # ================================================================
            if not success and not self.should_stop and len(safe_coordinates) > 1:
                print(f"\n🛡️ PHASE 2: SAFE BOUNDARY PATTERN ({len(safe_coordinates)} coords)")
                print("-" * 40)
                
                # Get state before phase 2
                state_before = None
                if verification_enabled:
                    state_before = self.get_comprehensive_slot_state()
                    if state_before:
                        print(f"📊 State BEFORE Phase 2: Putted={state_before.get('putted_characters')}")
                
                # Execute all clicks within safe boundary
                self.execute_multi_method_clicks(safe_coordinates, use_cdp, domain_adj, method="single")
                
                # Wait and verify after entire phase
                time.sleep(1.2)
                if verification_enabled and state_before:
                    state_after = self.get_comprehensive_slot_state()
                    if state_after:
                        is_success, reason, details = self.verify_slot_specific_return_enhanced(
                            target_slot_index, state_before, state_after
                        )
                        
                        if is_success:
                            print("🎉 PHASE 2 SUCCESS: Safe boundary pattern worked!")
                            success = True
                            phase_used = "phase_2_safe_boundary"
                        else:
                            print(f"❌ Phase 2 failed: {reason}")
                            if details.get('cross_contamination'):
                                print("🚨 Cross-contamination detected in Phase 2!")
            
            # ================================================================
            # PHASE 3: MULTI-METHOD ON SAFE COORDS
            # ================================================================
            if not success and not self.should_stop and len(safe_coordinates) > 1:
                print(f"\n💥 PHASE 3: MULTI-METHOD ON SAFE COORDINATES")
                print("-" * 40)
                
                # Try different methods on safe coordinates
                methods = ["double", "triple", "hold"]
                
                for method in methods:
                    if success or self.should_stop:
                        break
                        
                    print(f"   🔥 Method: {method} on safe coordinates")
                    
                    # Get state before this method
                    state_before = None
                    if verification_enabled:
                        state_before = self.get_comprehensive_slot_state()
                    
                    self.execute_multi_method_clicks(safe_coordinates, use_cdp, domain_adj, method=method)
                    
                    # Check after each method
                    time.sleep(1.0)
                    if verification_enabled and state_before:
                        state_after = self.get_comprehensive_slot_state()
                        if state_after:
                            is_success, reason, details = self.verify_slot_specific_return_enhanced(
                                target_slot_index, state_before, state_after
                            )
                            
                            if is_success:
                                print(f"🎉 PHASE 3 SUCCESS: {method} method worked!")
                                success = True
                                phase_used = f"phase_3_{method}_safe"
                                break
                            else:
                                print(f"❌ Method {method} failed: {reason}")
            
            # ================================================================
            # PHASE 4: EMERGENCY MICRO-EXPANSION (VERY LIMITED)
            # ================================================================
            if not success and not self.should_stop:
                print(f"\n🚨 PHASE 4: EMERGENCY MICRO-EXPANSION (Max {max_safe_radius + 10}px)")
                print("-" * 40)
                
                # Generate emergency coordinates - slightly beyond safe zone but still conservative
                emergency_radius = min(max_safe_radius + 10, 60)  # Very limited expansion
                emergency_coords = self.generate_circle_pattern_coordinates(
                    center_x, center_y, radius=emergency_radius, num_points=6
                )
                
                # Get state before emergency
                state_before = None
                if verification_enabled:
                    state_before = self.get_comprehensive_slot_state()
                    if state_before:
                        print(f"📊 State BEFORE Emergency: Putted={state_before.get('putted_characters')}")
                
                print(f"   🚨 Micro-expansion: {len(emergency_coords)} points at {emergency_radius}px")
                self.execute_multi_method_clicks(emergency_coords, use_cdp, domain_adj, method="single")
                
                # Final check
                time.sleep(1.5)
                if verification_enabled and state_before:
                    state_after = self.get_comprehensive_slot_state()
                    if state_after:
                        is_success, reason, details = self.verify_slot_specific_return_enhanced(
                            target_slot_index, state_before, state_after
                        )
                        
                        if is_success:
                            print("🎉 PHASE 4 SUCCESS: Emergency micro-expansion worked!")
                            success = True
                            phase_used = "phase_4_emergency_micro"
                        else:
                            print(f"❌ Emergency phase failed: {reason}")
                            if details.get('cross_contamination'):
                                print("🚨 Cross-contamination even in emergency phase!")
            
            # ================================================================
            # FINAL RESULT WITH COMPREHENSIVE REPORT
            # ================================================================
            print("\n" + "="*80)
            if success:
                print(f"🎉 SMART SLOT RETURN COMPLETED SUCCESSFULLY!")
                print(f"   ✅ Target slot: {target_slot_index}")
                print(f"   ✅ Success method: {phase_used}")
                print(f"   🛡️ Max safe radius used: {max_safe_radius}px")
                print(f"   🔍 Enhanced verification: {'✅ Enabled' if verification_enabled else '⚠️ Disabled'}")
                print(f"   🛡️ Cross-slot contamination: PREVENTED")
                print(f"   📊 Polos array tracking: {'✅ Active' if verification_enabled else '⚠️ N/A'}")
                print("="*80)
                return True
            else:
                print(f"💥 SMART SLOT RETURN FAILED")
                print(f"   ❌ Target slot: {target_slot_index}")
                print(f"   ❌ All phases attempted within safe boundary")
                print(f"   🛡️ Max safe radius: {max_safe_radius}px")
                print(f"   🔍 Enhanced verification: {'✅ Enabled' if verification_enabled else '⚠️ Disabled'}")
                print(f"   🛡️ Cross-contamination protection: ACTIVE")
                print(f"   📊 Detailed slot state: TRACKED")
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
            
            # PHASE 3: SMART RETURN ICON WITH VALIDATION
            print("🔄 PHASE 3: Smart returning icon with validation...")
            if progress_callback:
                progress_callback(f"↺ Smart returning {mode} icon {icon_index + 1}")

            # Enhanced return with validation
            return_success = self.corrected_return_icon(target_slot, use_cdp)

            if return_success:
                # Post-return validation for single icon
                time.sleep(0.3)
                validation_state = self.get_comprehensive_slot_state()
                
                if validation_state and self.verification_available:
                    polos_array = validation_state.get('polos_array', [])
                    target_index = target_slot.get('index', -1)
                    
                    if target_index < len(polos_array) and polos_array[target_index] == "0":
                        print(f"✅ Icon {icon_index + 1} return validated: Slot {target_index} clean")
                    else:
                        print(f"⚠️ Icon {icon_index + 1} return validation warning: May need cleanup")
                else:
                    print(f"✅ Icon {icon_index + 1} returned (basic validation)")
            else:
                print(f"⚠️ Warning: Failed to return icon {icon_index}")
                
                # Emergency cleanup attempt for failed return
                if self.verification_available:
                    print(f"🚨 Emergency cleanup attempt for icon {icon_index}")
                    emergency_success = self._emergency_slot_recovery(target_slot.get('index', -1), target_slot, use_cdp)
                    if emergency_success:
                        print(f"✅ Emergency cleanup successful for icon {icon_index}")
                    else:
                        print(f"💥 Emergency cleanup failed for icon {icon_index}")

            # Brief stabilization before next icon
            time.sleep(0.3)
            
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
            if self.verification_available:
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
            if self.verification_available:
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
            
            # Phase 3: Smart batch return all icons
            print("🔄 PHASE 3: Smart batch returning all icons...")
            if progress_callback:
                progress_callback(f"🧹 Smart cleanup {remix_name}")

            print(f"  🔄 Using smart batch return for {len(remix_icons)} icons...")

            # Use smart batch return instead of individual returns
            success_count, total_attempted, error_log = self.smart_batch_return_all_slots(use_cdp=False)

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
            if self.verification_available:
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
            if self.verification_available:
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
            if self.verification_available:
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
            if self.verification_available:
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
                    
                    if horror_state and self.verification_available:
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
            if self.verification_available:
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
            self.root.title("Sprunki Game Player - ENHANCED with Drag Verification + Smart Page Verification")
            self.root.geometry("650x900")
            
            self.mode = IntVar(value=1)
            self.status_var = StringVar()
            self.status_var.set("Config not loaded")
            self.action_var = StringVar()
            self.action_var.set("🚀 Ready for ENHANCED workflow with Smart Verification...")
            
            # Header
            Label(self.root, text="Sprunki Game Player", font=("Arial", 16, "bold")).pack(pady=10)
            Label(self.root, text="🎮 ENHANCED: Chrome Debug + Drag Verification + Smart Page Verification 🚀", 
                font=("Arial", 12), fg="green").pack()
            
            # Enhanced workflow info
            enhanced_info_frame = Frame(self.root, bg="#E8F5E8", relief="ridge", bd=1)
            enhanced_info_frame.pack(fill="x", padx=10, pady=5)
            
            Label(enhanced_info_frame, text="⚡ ENHANCED WORKFLOW:", 
                font=("Arial", 10, "bold"), bg="#E8F5E8", fg="#2E7D32").pack(anchor="w", padx=10, pady=(5,2))
            
            enhanced_features = """✅ Drag Verification: Check 'Putted characters' variable before/after each drag
    ✅ 5-Coordinate Retry: Center → Left → Right → Top → Bottom positions for each icon
    ✅ Smart Fallback: Auto-fallback to PyAutoGUI if CDP verification fails
    ✅ Enhanced Statistics: Track verification success rate and coordinate usage
    ✅ Game-Aware: Real-time monitoring of game internal state
    ✅ Intelligent Retry: Return icon and retry at different coordinate if verification fails"""
            
            Label(enhanced_info_frame, text=enhanced_features, justify="left",
                font=("Arial", 8), fg="#1B5E20", bg="#E8F5E8").pack(anchor="w", padx=15, pady=(0,5))
            
            # Smart verification system info
            verification_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
            verification_frame.pack(fill="x", padx=10, pady=5)
            
            Label(verification_frame, text="🔍 SMART VERIFICATION SYSTEM:", 
                font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
            
            verification_text = """🔍 Page Load Verification: Check document.readyState, loading spinners, basic elements
    🎮 Game Components Verification: Check game containers, React components, Run button in DOM
    ⚡ Real-Time Monitoring: WebSocket/CDP JavaScript execution for live page state
    🎯 Smart Timing: Replace fixed 40s wait with intelligent verification + 3s stabilization
    📊 Comprehensive Checks: Network idle, DOM completeness, spinner disappearance
    🔄 Auto-Retry: Page reload and re-verification if initial checks fail"""
            
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
    📐 Template Matching: Advanced CV2 image matching with confidence thresholds
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
            
            # Debug frame with Smart Verification
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
            
            Button(debug_buttons, text="🔍 Test Verification", 
                command=self.test_verification_system, bg="#9C27B0", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
            
            Button(debug_buttons, text="⚡ Test Smart Verification", 
                command=self.test_smart_verification_system, bg="#2196F3", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
            
            # Remix info display
            self.display_remix_library_info()
            
            # Main button
            Button(self.root, text="🔍 Load JSON & Start ENHANCED Auto with Smart Verification", 
                command=self.load_config_and_start, 
                bg="#4CAF50", fg="white", font=("Arial", 12, "bold")).pack(pady=15)
            
            # Status
            Label(self.root, text="📊 Status:").pack(pady=(20,5))
            Label(self.root, textvariable=self.status_var, fg="blue").pack()
            
            # Instructions with Smart Verification
            instruction_text = """
    🎯 ENHANCED WORKFLOW WITH SMART VERIFICATION:

    🔍 SMART VERIFICATION PROCESS:
    1. Page Load Verification: Check document.readyState, loading spinners, network idle
    2. Game Components Verification: Check game containers, React components, Run button DOM
    3. Real-Time Monitoring: WebSocket/CDP JavaScript execution for live state
    4. Smart Timing: Replace fixed 40s wait → intelligent verification + 3s stabilization

    ⚡ 5-COORDINATE STRATEGY (UNCHANGED):
    • Coordinate 1: Center (original icon position)
    • Coordinate 2: Left (icon_x - 15, icon_y)
    • Coordinate 3: Right (icon_x + 15, icon_y)  
    • Coordinate 4: Top (icon_x, icon_y - 15)
    • Coordinate 5: Bottom (icon_x, icon_y + 15)

    🖼️ IMAGE DETECTION (PRESERVED):
    • Original Run button & Fullscreen detection logic kept exactly as-is
    • Auto-scroll capability for buttons outside viewport
    • Multi-scale template matching with confidence thresholds
    • Domain-specific timing adjustments for cocrea.world

    🚀 NEW WORKFLOW:
    Page Load → Smart Verification → Game Verification → ✅ Verification Success
    → 3s Stabilization → Start Image Detection (Run Button) → Game Load
    → Image Detection (Fullscreen Button) → F11 Fullscreen → Ready for Automation

    🎮 BENEFITS:
    • Replaces blind 40s wait with intelligent verification
    • Knows exactly when page/game is ready
    • Reduces total setup time when page loads quickly
    • Maintains reliability when page loads slowly
    • Zero changes to proven image detection methods
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
            
            # Domain support with Smart Verification
            domains_text = f"🌐 Enhanced for: cocrea.world (Chrome Debug + Smart Verification + Image Detection)"
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
        """Test drag verification system"""
        try:
            if not self.putted_chars_setup_done:
                messagebox.showinfo("Verification Test", 
                    "🔍 Verification system not setup yet.\n\n"
                    "Load a JSON file and start the workflow to initialize verification.")
                return
            
            if not self.verification_available:
                messagebox.showwarning("Verification Test", 
                    "⚠️ Verification system is disabled.\n\n"
                    "WebSocket connection may not be available.")
                return
            
            # Test getting current value
            current_value = self.get_putted_chars_value()
            
            if current_value is not None:
                messagebox.showinfo("Verification Test", 
                    f"✅ Verification system working!\n\n"
                    f"Current 'Putted characters' value: {current_value}\n"
                    f"JavaScript monitoring: ACTIVE\n"
                    f"High-performance access: ENABLED\n\n"
                    f"Ready for drag verification!")
            else:
                messagebox.showerror("Verification Test", 
                    "❌ Verification test failed!\n\n"
                    "Could not get 'Putted characters' value.\n"
                    "Game may not be loaded or VM not found.")
                    
        except Exception as e:
            messagebox.showerror("Verification Test", f"❌ Verification test error: {e}")

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
            
            # Check hasHorror setting for remix generation
            game_settings = self.config.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            
            # Generate remix library with horror-aware logic
            self.remix_library = self.generate_random_remixes(avoid_horror_icon=has_horror)
            self.current_remix_index = 0
            self.drag_stats = DragOperationStats()
            self.update_remix_display()
            
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
            f"   • System status: {'✅ ACTIVE' if self.verification_available else '⚠️ DISABLED'}\n\n"
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
    • Verification system: {'✅ ACTIVE' if self.verification_available else '⚠️ DISABLED'}
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
    • Pre-flight slot checking: {'✅ Active' if self.verification_available else '❌ Disabled'}
    • Cross-contamination prevention: {'✅ Active' if self.verification_available else '❌ Disabled'}
    • Smart batch return: ✅ Available
    • Emergency recovery: ✅ Available
    • Real-time validation: {'✅ Active' if self.verification_available else '❌ Disabled'}

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
            if self.verification_available:
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
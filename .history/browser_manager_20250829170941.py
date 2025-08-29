# browser_manager.py
"""
Browser Management for Sprunki Game Player
Handles Chrome debug process, WebSocket connections, CDP commands,
and all browser-level interactions.
"""
import os
import subprocess
import time
import json
import random
from tkinter import messagebox
import pyautogui
import requests
import websocket
import psutil
import pygetwindow as gw
import shutil
import getpass
import socket
import traceback
from typing import Optional, TYPE_CHECKING 
if TYPE_CHECKING:
    from sprunki_game_player import SprunkiGamePlayer

class BrowserManager:
    # [PHASE 3] FIX - Explicitly type self.player as Optional
    def __init__(self, player_instance: Optional['SprunkiGamePlayer'], event_bus=None, config=None):
        print("🚀 BrowserManager initialized.")
        self.player = player_instance
        self.event_bus = event_bus
        self.config = config
        
        # [PHASE 3] Use config directly if player_instance is None
        if player_instance:
            self.app_config = self.player.app_config
        elif config:
            # Tạo một dict tạm thời để các logic cũ không bị lỗi
            self.app_config = {
                'chrome': {
                    'executable_paths': config.chrome.executable_paths,
                    'debugging_port': config.chrome.debugging_port,
                    'user_data_dir': config.chrome.user_data_dir
                },
                'game': {
                    'page_load_wait': config.timing.page_load_wait,
                    'fullscreen_wait': config.timing.fullscreen_wait,
                    'intro_skip_clicks': config.timing.intro_skip_clicks
                },
                'page_verification': {
                    'stabilization_wait_seconds': 3 # Giả định
                }
            }
        else:
            raise ValueError("BrowserManager requires either a player_instance or a config object.")

        # Trạng thái của trình duyệt
        self.chrome_process = None
        self.ws_url = None
        
        # Các thuộc tính cấu hình được chuyển từ SprunkiGamePlayer
        self.chrome_config = self.app_config.get('chrome', {})
        self.chrome_paths = self.chrome_config.get('executable_paths', [])
        self.debugging_port = self.chrome_config.get('debugging_port', 9222)
        self.user_data_dir = self.chrome_config.get('user_data_dir', 'chrome_permanent_profile')
        
        self.game_config = self.app_config.get('game', {})
        self.page_load_wait = self.game_config.get('page_load_wait', 20)
        self.fullscreen_wait = self.game_config.get('fullscreen_wait', 8)
        self.stabilization_wait = self.app_config.get('page_verification', {}).get('stabilization_wait_seconds', 3)
        self.intro_skip_clicks = self.game_config.get('intro_skip_clicks', 10)
        
        # JavaScript để tìm nút Skip Intro
        self.js_find_skip_intro = '''
        new Promise(async (resolve, reject) => {
            try {
                console.log("ðŸ” [GAME PLAYER] Searching for ALL Skip Intro buttons...");
                
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
                    console.log("âŒ [GAME PLAYER] VM not found - cannot search for Skip Intro buttons");
                    resolve({ found: false, count: 0, buttons: [], reason: "vm_not_found" });
                    return;
                }
                
                const allTargets = vm.runtime.targets.map(t => t);
                console.log(`ðŸŽ¯ [GAME PLAYER] Total targets found: ${allTargets.length}`);
                
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
                
                console.log(`ðŸ” [GAME PLAYER] Found ${introTargets.length} 'Intro' targets with 'Skip' costume`);
                
                if (introTargets.length === 0) {
                    console.log("â„¹ï¸ [GAME PLAYER] No Skip Intro buttons found");
                    resolve({ found: false, count: 0, buttons: [], reason: "target_not_found" });
                    return;
                }
                
                // Process ALL intro targets
                const skipIntroButtons = [];
                
                introTargets.forEach((target, index) => {
                    console.log(`\\nðŸŽ¯ [GAME PLAYER] Processing Intro Target ${index + 1}/${introTargets.length}:`);
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
                        console.log(`   âœ… [GAME PLAYER] Converted to pixel: (${pixelCoords.x}, ${pixelCoords.y}) - ${target.visible ? 'VISIBLE' : 'HIDDEN'}`);
                    }
                });
                
                const visibleButtons = skipIntroButtons.filter(btn => btn.visible);
                
                console.log(`\\nðŸ“Š [GAME PLAYER] BUTTONS SUMMARY: Total: ${skipIntroButtons.length}, Visible: ${visibleButtons.length}`);
                
                resolve({
                    found: skipIntroButtons.length > 0,
                    count: skipIntroButtons.length,
                    buttons: skipIntroButtons,
                    visibleCount: visibleButtons.length,
                    reason: skipIntroButtons.length > 0 ? "success" : "no_valid_buttons"
                });
                
            } catch(e) { 
                console.error("âŒ [GAME PLAYER] Error searching for Skip Intro buttons:", e);
                resolve({ found: false, count: 0, buttons: [], reason: "error", error: e.toString() }); 
            }
        });
        '''

    def _execute_verification_js(self, js_code):
        """Execute JavaScript for verification via WebSocket/CDP"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:  # âœ… CHECK None trÆ°á»›c
                print("âš ï¸ WebSocket not available for verification, skipping JS check")
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)  # â† ÄÃƒ SAFE
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_code, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')
                return result
            else:
                print("âš ï¸ No response from verification JavaScript")
                return None
                
        except Exception as e:
            print(f"âš ï¸ Error executing verification JavaScript: {e}")
            return None

    def verify_page_fully_loaded(self, target_url):
        """
        ðŸ” Smart Page Load Verification System
        Checks if page is fully loaded before proceeding with image detection
        """
        try:
            if not self.player.page_verification_enabled:
                print("ðŸ” Page verification disabled, assuming page ready")
                return True
            
            print(f"\nðŸ” SMART PAGE VERIFICATION: {target_url}")
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
            max_retries = self.player.page_verification_retries
            
            for attempt in range(1, max_retries + 1):
                if self.player.should_stop:
                    return False
                
                print(f"ðŸ” Page verification attempt {attempt}/{max_retries}")
                
                # Execute verification JavaScript
                verification_result = self._execute_verification_js(verification_js)
                
                if verification_result:
                    document_state = verification_result.get('documentState', 'unknown')
                    basic_elements = verification_result.get('basicElements', {})
                    loading_spinners = verification_result.get('loadingSpinners', {})
                    ready_score = verification_result.get('readyScore', 0)
                    is_ready = verification_result.get('isReady', False)
                    
                    print(f"   ðŸ“Š Document state: {document_state}")
                    print(f"   ðŸ“Š Basic elements: Body={basic_elements.get('hasBody')}, Head={basic_elements.get('hasHead')}, Children={basic_elements.get('bodyChildren', 0)}")
                    print(f"   ðŸ“Š Loading spinners: {loading_spinners.get('count', 0)} active")
                    print(f"   ðŸ“Š Ready score: {ready_score}/100")
                    print(f"   ðŸ“Š Overall assessment: {'âœ… READY' if is_ready else 'â³ NOT READY'}")
                    
                    if is_ready:
                        elapsed = time.time() - start_time
                        print(f"âœ… PAGE VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if loading_spinners.get('count', 0) > 0:
                            spinner_types = ', '.join(loading_spinners.get('types', []))
                            print(f"   â³ Waiting for loading spinners to disappear: {spinner_types}")
                        if document_state != 'complete':
                            print(f"   â³ Waiting for document to complete loading...")
                        if basic_elements.get('bodyChildren', 0) == 0:
                            print(f"   â³ Waiting for page content to render...")
                
                else:
                    print(f"   âš ï¸ Verification JavaScript failed, using fallback check")
                    # Fallback: Simple document ready check
                    simple_check = self._execute_verification_js("document.readyState")
                    if simple_check == 'complete':
                        print(f"   âœ… Fallback check passed: document.readyState = complete")
                        return True
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.player.page_verification_timeout:
                    print(f"âš ï¸ Page verification timeout ({self.player.page_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with image detection...")
                    return True  # Don't fail completely, proceed with image detection
                
                # Wait before next attempt
                if attempt < max_retries:
                    print(f"   â³ Waiting {self.player.page_verification_interval}s before next check...")
                    time.sleep(self.player.page_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"âš ï¸ Page verification failed after {max_retries} attempts")
            print("   Proceeding anyway with image detection...")
            return True
            
        except Exception as e:
            print(f"âš ï¸ Error in page verification: {e}")
            print("   Proceeding anyway with image detection...")
            return True  # Don't block workflow on verification errors

    def verify_game_components_ready(self):
        """
        ðŸŽ® Smart Game Load Verification System
        Checks if game components are ready before proceeding with Run button detection
        """
        try:
            if not self.player.game_verification_enabled:
                print("ðŸŽ® Game verification disabled, assuming game ready")
                return True
            
            print(f"\nðŸŽ® SMART GAME VERIFICATION")
            print("="*70)
            
            # JavaScript for game component verification
            game_verification_js = f"""
            (() => {{
                // ðŸ†• EXPANDED: More comprehensive game container selectors
                const selectors = [
                    ".ccw-stage-wrapper", 
                    "#stage-canvas-wrapper", 
                    "canvas",
                    ".style_gandi-player-wrapper__WCs0P",
                    ".style_stage-player__0Xy7d",
                    ".style_stage-cover__VITCW",
                    ".gandi_stage_stage_1fD7k",
                    "[class*='stage']",
                    "[class*='player']",
                    "[class*='game']",
                    "#app canvas",
                    ".app-container canvas",
                    "iframe[src*='scratch']",
                    "iframe[src*='game']"
                ];
                
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
                
                // Check game container elements with more detailed logging
                selectors.forEach(selector => {{
                    try {{
                        const elements = document.querySelectorAll(selector);
                        if (elements.length > 0) {{
                            elements.forEach((el, index) => {{
                                const rect = el.getBoundingClientRect();
                                const containerInfo = {{
                                    selector: selector,
                                    index: index,
                                    id: el.id || 'no-id',
                                    className: el.className || 'no-class',
                                    tagName: el.tagName.toLowerCase(),
                                    visible: rect.width > 0 && rect.height > 0,
                                    hasChildren: el.children.length > 0,
                                    dimensions: `${{rect.width}}x${{rect.height}}`
                                }};
                                results.gameContainers.push(containerInfo);
                                console.log(`ðŸŽ¯ Found game container: ${{selector}} - ${{containerInfo.tagName}} - ${{containerInfo.dimensions}} - Visible: ${{containerInfo.visible}}`);
                            }});
                        }}
                    }} catch (e) {{
                        console.warn(`Selector failed: ${{selector}} - ${{e.message}}`);
                    }}
                }});
                
                // Check for React components
                const checkReactComponents = () => {{
                    const elements = document.querySelectorAll('*');
                    let reactFound = false;
                    let reactCount = 0;
                    
                    for (let el of elements) {{
                        const keys = Object.keys(el);
                        const reactKey = keys.find(key => key.startsWith('__react'));
                        if (reactKey) {{
                            reactFound = true;
                            reactCount++;
                            if (reactCount <= 5) {{ // Log first 5 only
                                results.reactComponents.details.push({{
                                    element: el.tagName,
                                    id: el.id || 'no-id',
                                    className: el.className || 'no-class',
                                    reactKey: reactKey.substring(0, 20) + '...'
                                }});
                            }}
                        }}
                        if (reactCount >= 100) break; // Stop after finding many
                    }}
                    results.reactComponents.found = reactFound;
                    results.reactComponents.count = reactCount;
                    console.log(`âš›ï¸ React check: Found ${{reactCount}} components`);
                }};
                
                if ({str(self.player.check_react_components).lower()}) {{
                    checkReactComponents();
                }}
                
                // ðŸ†• ENHANCED: More comprehensive Run button detection
                if ({str(self.player.check_run_button_dom).lower()}) {{
                    const runButtonSelectors = [
                        'button[type="submit"]',
                        'button[class*="run"]',
                        'button[class*="play"]', 
                        'button[class*="start"]',
                        '.style_run-button__VjFSY',
                        '.MuiButton-contained',
                        'button:contains("Run")',
                        'button:contains("Play")',
                        'button:contains("Start")',
                        'button:contains("â–¶")',
                        '[aria-label*="run"]',
                        '[aria-label*="play"]',
                        '[aria-label*="start"]',
                        'input[type="submit"]',
                        'input[value*="Run"]',
                        'input[value*="Play"]',
                        '.run-button',
                        '#run-button',
                        '.play-button',
                        '#play-button'
                    ];
                    
                    let foundButtons = [];
                    
                    for (let selector of runButtonSelectors) {{
                        try {{
                            const buttons = document.querySelectorAll(selector);
                            for (let button of buttons) {{
                                const text = (button.textContent || button.value || button.alt || '').toLowerCase();
                                const hasRunText = text.includes('run') || text.includes('play') || text.includes('start') || text.includes('â–¶');
                                const isVisible = button.offsetParent !== null;
                                
                                if (hasRunText || selector.includes('run') || selector.includes('play')) {{
                                    foundButtons.push({{
                                        selector: selector,
                                        text: text.substring(0, 20),
                                        visible: isVisible,
                                        className: button.className || 'no-class'
                                    }});
                                    
                                    if (hasRunText || isVisible) {{
                                        results.runButtonInDOM = true;
                                        console.log(`ðŸ”˜ Found Run button: ${{selector}} - "${{text}}" - Visible: ${{isVisible}}`);
                                    }}
                                }}
                            }}
                        }} catch (e) {{
                            // Skip invalid selectors silently
                        }}
                    }}
                    
                    console.log(`ðŸ”˜ Run button search: Found ${{foundButtons.length}} candidates, runButtonInDOM: ${{results.runButtonInDOM}}`);
                }}
                
                // Assessment with more lenient criteria
                const gameContainersFound = results.gameContainers.length >= {self.player.min_containers_required};
                const reactReady = !{str(self.player.check_react_components).lower()} || results.reactComponents.found;
                const runButtonReady = !{str(self.player.check_run_button_dom).lower()} || results.runButtonInDOM;
                
                results.overallReady = gameContainersFound && reactReady && runButtonReady;
                
                // ðŸ†• DETAILED: Log comprehensive results
                console.log(`ðŸŽ¯ Game Verification Summary:`);
                console.log(`   - Game containers: ${{results.gameContainers.length}} (need ${{self.min_containers_required}})`);
                console.log(`   - React components: ${{results.reactComponents.found}} (${{results.reactComponents.count || 0}} found)`);
                console.log(`   - Run button in DOM: ${{results.runButtonInDOM}}`);
                console.log(`   - Overall ready: ${{results.overallReady}}`);
                
                return results;
            }})();
            """
            
            start_time = time.time()
            max_attempts = 3
            
            for attempt in range(1, max_attempts + 1):
                if self.player.should_stop:
                    return False
                
                print(f"ðŸŽ® Game verification attempt {attempt}/{max_attempts}")
                
                # Execute game verification JavaScript
                game_result = self._execute_verification_js(game_verification_js)
                
                if game_result:
                    game_containers = game_result.get('gameContainers', [])
                    react_components = game_result.get('reactComponents', {})
                    run_button_in_dom = game_result.get('runButtonInDOM', False)
                    overall_ready = game_result.get('overallReady', False)
                    
                    print(f"   ðŸŽ¯ Game containers found: {len(game_containers)}")
                    for container in game_containers[:3]:  # Show first 3
                        print(f"      - {container['selector']}: visible={container['visible']}, children={container['hasChildren']}")
                    
                    if self.player.check_react_components:
                        react_found = react_components.get('found', False)
                        react_count = len(react_components.get('details', []))
                        print(f"   âš›ï¸ React components: {'âœ… Found' if react_found else 'âŒ Not found'} ({react_count} detected)")
                    
                    if self.player.check_run_button_dom:
                        print(f"   ðŸ”˜ Run button in DOM: {'âœ… Found' if run_button_in_dom else 'âŒ Not found'}")
                    
                    print(f"   ðŸŽ¯ Overall game ready: {'âœ… YES' if overall_ready else 'âŒ NO'}")
                    
                    if overall_ready:
                        elapsed = time.time() - start_time
                        print(f"âœ… GAME VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if len(game_containers) < self.player.min_containers_required:
                            print(f"   â³ Waiting for game containers to load ({len(game_containers)}/{self.player.min_containers_required})")
                        if self.player.check_react_components and not react_components.get('found'):
                            print(f"   â³ Waiting for React components to initialize")
                        if self.player.check_run_button_dom and not run_button_in_dom:
                            print(f"   â³ Waiting for Run button to appear in DOM")
                
                else:
                    print(f"   âš ï¸ Game verification JavaScript failed")
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.player.game_verification_timeout:
                    print(f"âš ï¸ Game verification timeout ({self.player.game_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with image detection...")
                    return True
                
                # Wait before next attempt
                if attempt < max_attempts:
                    print(f"   â³ Waiting {self.player.game_verification_interval}s before next check...")
                    time.sleep(self.player.game_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"âš ï¸ Game verification failed after {max_attempts} attempts")
            print("   Proceeding anyway with image detection...")
            return True
            
        except Exception as e:
            print(f"âš ï¸ Error in game verification: {e}")
            print("   Proceeding anyway with image detection...")
            return True

    def verify_page_fully_loaded_lightweight(self, target_url):
        """Lightweight version for continuous monitoring"""
        try:
            # Simplified check for continuous use
            verification_js = """
            (() => {
                const results = {
                    documentState: document.readyState,
                    hasBody: !!document.body,
                    hasHead: !!document.head,
                    bodyChildren: document.body ? document.body.children.length : 0,
                    loadingSpinners: 0
                };
                
                // Check for loading spinners
                const spinnerSelectors = ['.loading', '.spinner', '.loader', '[class*="loading"]'];
                spinnerSelectors.forEach(selector => {
                    const elements = document.querySelectorAll(selector);
                    const visibleSpinners = Array.from(elements).filter(el => {
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                    });
                    results.loadingSpinners += visibleSpinners.length;
                });
                
                // Calculate readiness score
                let score = 0;
                if (results.documentState === 'complete') score += 40;
                if (results.hasBody && results.hasHead) score += 30;
                if (results.bodyChildren > 0) score += 20;
                if (results.loadingSpinners === 0) score += 10;
                
                results.score = score;
                results.ready = score >= 75;
                
                return results;
            })();
            """
            
            result = self._execute_verification_js(verification_js)
            if result:
                print(f"      ðŸ“Š Page score: {result.get('score', 0)}/100, Spinners: {result.get('loadingSpinners', 'N/A')}")
                return result.get('ready', False)
            return False
            
        except Exception as e:
            print(f"   âš ï¸ Page check error: {e}")
            return False


    def verify_game_components_ready_lightweight(self):
        """Lightweight version for continuous monitoring"""
        try:
            # Simplified game check
            game_js = """
            (() => {
                const results = {
                    gameContainers: 0,
                    reactComponents: 0,
                    runButton: false
                };
                
                // Check game containers
                const containerSelectors = ['.ccw-stage-wrapper', 'canvas', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                containerSelectors.forEach(selector => {
                    const elements = document.querySelectorAll(selector);
                    elements.forEach(el => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results.gameContainers++;
                        }
                    });
                });
                
                // Check React components
                const allElements = document.querySelectorAll('*');
                for (let el of allElements) {
                    const keys = Object.keys(el);
                    if (keys.find(key => key.startsWith('__react'))) {
                        results.reactComponents++;
                        if (results.reactComponents >= 5) break; // Enough sample
                    }
                }
                
                // Check Run button
                const runButtonSelectors = [
                    '.style_run-button__VjFSY',
                    'button[class*="run"]',
                    'button.MuiButton-contained'
                ];
                
                for (const selector of runButtonSelectors) {
                    const button = document.querySelector(selector);
                    if (button) {
                        results.runButton = true;
                        break;
                    }
                }
                
                // Calculate readiness
                results.ready = results.gameContainers > 0 && results.runButton;
                
                return results;
            })();
            """
            
            result = self._execute_verification_js(game_js)
            if result:
                containers = result.get('gameContainers', 0)
                has_button = result.get('runButton', False)
                print(f"      ðŸ“Š Containers: {containers}, Run button: {'âœ…' if has_button else 'âŒ'}")
                return result.get('ready', False)
            return False
            
        except Exception as e:
            print(f"   âš ï¸ Game check error: {e}")
            return False

    # ================================================================
    # == CHROME DEBUG BROWSER MANAGEMENT (from json_generator.py) ==
    # ================================================================

    def cleanup_chrome_complete(self):
        """
        🔧 FIXED: Force complete browser shutdown with port cleanup verification
        """
        try:
            print("🔧 ENHANCED: Force complete browser shutdown with port verification...")
            
            # Phase 1: Close WebSocket connections immediately
            if hasattr(self, 'session_websocket') and self.session_websocket:
                try:
                    self.session_websocket.close()
                    print("   ✅ WebSocket connection closed")
                except:
                    pass
            
            # Phase 2: Kill Chrome processes using port 9222 specifically
            try:
                import subprocess
                import psutil
                
                print(f"   🔍 Checking processes using port {self.debugging_port}...")
                
                # Find all Chrome processes with our debug port
                chrome_pids_to_kill = []
                
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] and 'chrome' in proc.info['name'].lower():
                            cmdline = proc.info.get('cmdline', [])
                            if cmdline:
                                cmdline_str = ' '.join(cmdline)
                                if f'--remote-debugging-port={self.debugging_port}' in cmdline_str:
                                    chrome_pids_to_kill.append(proc.info['pid'])
                                    print(f"   🎯 Found Chrome debug process: PID {proc.info['pid']}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                
                # Kill identified processes
                for pid in chrome_pids_to_kill:
                    try:
                        proc = psutil.Process(pid)
                        proc.terminate()
                        print(f"   ✅ Terminated Chrome debug process: PID {pid}")
                    except:
                        try:
                            proc = psutil.Process(pid)
                            proc.kill()
                            print(f"   ⚡ Killed Chrome debug process: PID {pid}")
                        except:
                            pass
                
                # Wait for processes to fully terminate
                if chrome_pids_to_kill:
                    print(f"   ⏳ Waiting for {len(chrome_pids_to_kill)} processes to terminate...")
                    psutil.wait_procs([psutil.Process(pid) for pid in chrome_pids_to_kill if psutil.pid_exists(pid)], timeout=5)
                
            except ImportError:
                print("   ⚠️ psutil not available, using fallback method")
                # Fallback: terminate via process reference
                if hasattr(self, 'chrome_process') and self.chrome_process:
                    try:
                        self.chrome_process.terminate()
                        self.chrome_process.wait(timeout=3)
                        print("   ✅ Chrome process terminated via reference")
                    except:
                        try:
                            self.chrome_process.kill()
                            print("   ⚡ Chrome process killed via reference")
                        except:
                            pass
            
            # Phase 3: Verify port 9222 is released
            print(f"   🔍 Verifying port {self.debugging_port} is released...")
            port_released = False
            
            for attempt in range(10):  # Try for 10 seconds
                try:
                    import socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('localhost', self.debugging_port))
                    sock.close()
                    
                    if result != 0:  # Port is not accessible = released
                        port_released = True
                        print(f"   ✅ Port {self.debugging_port} confirmed released")
                        break
                    else:
                        print(f"   ⏳ Port {self.debugging_port} still in use, waiting... (attempt {attempt + 1}/10)")
                        time.sleep(1)
                        
                except Exception as e:
                    print(f"   ⚠️ Port check error: {e}")
                    time.sleep(1)
            
            if not port_released:
                print(f"   ⚠️ Port {self.debugging_port} may still be in use after 10s")
                
                # Emergency: try to kill any remaining process on port
                try:
                    import subprocess
                    # Use netstat to find process using port 9222
                    result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
                    for line in result.stdout.split('\n'):
                        if f':{self.debugging_port}' in line and 'LISTENING' in line:
                            parts = line.split()
                            if parts:
                                pid = parts[-1]
                                try:
                                    subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
                                    print(f"   ⚡ Emergency killed process {pid} using port {self.debugging_port}")
                                except:
                                    pass
                except:
                    pass
            
            # Phase 4: Close windows by title (keep existing logic)
            try:
                import pygetwindow as gw
                for title_part in ['cocrea.world', 'Chrome']:
                    windows = gw.getWindowsWithTitle(title_part)
                    for window in windows:
                        try:
                            window.close()
                            print(f"   ✅ Closed window: {window.title[:50]}...")
                        except:
                            pass
            except:
                print("   ⚠️ Could not close windows via pygetwindow")
            
            # Phase 5: Reset internal state
            self.ws_url = None
            self.chrome_process = None
            self.session_websocket = None
            self.session_websocket_url = None
            self.batch_verification_active = False
            
            print("✅ ENHANCED browser shutdown completed with port verification")
            
        except Exception as e:
            print(f"🚫 Enhanced browser shutdown error: {e}")
            # Still reset state even if error
            self.ws_url = None
            self.chrome_process = None
            self.session_websocket = None
            self.batch_verification_active = False

    def clear_cache_cookies_only(self):
        """ðŸ†• Clear only cache and cookies, keep profile/login intact"""
        try:
            import shutil
            import glob
            
            print("ðŸ—‘ï¸ Clearing cache and cookies only (keeping profile intact)...")
            
            user_data_path = os.path.join(os.getcwd(), self.user_data_dir)
            if not os.path.exists(user_data_path):
                print(f"   â„¹ï¸ Profile directory not found: {user_data_path}")
                return
                
            default_path = os.path.join(user_data_path, "Default")
            if not os.path.exists(default_path):
                print("   â„¹ï¸ Default profile not found")
                return
                
            # Clear only temporary cache files, PRESERVE login/extension data
            cache_paths = [
                os.path.join(default_path, "Cache", "Cache_Data"),  # Browser cache only
                os.path.join(default_path, "Code Cache"),           # JS compiled cache only  
                os.path.join(default_path, "GPUCache"),             # GPU cache only
                # âŒ REMOVED: Cookies - giá»¯ Ä‘á»ƒ duy trÃ¬ login
                # âŒ REMOVED: Cookies-journal - giá»¯ Ä‘á»ƒ duy trÃ¬ login
                # âŒ REMOVED: Sessions - giá»¯ Ä‘á»ƒ duy trÃ¬ active sessions
                # âŒ REMOVED: Local Storage - extensions cáº§n dá»¯ liá»‡u nÃ y
                # âŒ REMOVED: IndexedDB - extensions lÆ°u settings á»Ÿ Ä‘Ã¢y
                # âŒ REMOVED: Service Worker - extensions hoáº¡t Ä‘á»™ng qua SW
                # âŒ REMOVED: Network - giá»¯ Ä‘á»ƒ extension hoáº¡t Ä‘á»™ng ngay
            ]
            
            cleared_count = 0
            for cache_path in cache_paths:
                try:
                    if os.path.exists(cache_path):
                        if os.path.isfile(cache_path):
                            os.remove(cache_path)
                        elif os.path.isdir(cache_path):
                            shutil.rmtree(cache_path, ignore_errors=True)
                        cleared_count += 1
                        print(f"   âœ… Cleared: {os.path.basename(cache_path)}")
                except Exception as e:
                    print(f"   âš ï¸ Could not clear {cache_path}: {e}")
                    
            print(f"   ðŸ§¹ Minimal cache cleanup completed - {cleared_count} items processed")
            print("   âœ… Login session, cookies, extensions data preserved")
            print("   ðŸ“¦ Extensions should work immediately without refresh")
            
        except Exception as e:
            print(f"âŒ Cache clearing error: {e}")

    def force_clear_chrome_data(self):
        """ðŸ†• FORCE: Clear Chrome data even when Chrome is not running"""
        try:
            # ðŸ”§ FIX: Import modules á»Ÿ Ä‘áº§u hÃ m Ä‘á»ƒ trÃ¡nh "possibly unbound"
            import shutil
            import getpass
            
            print("ðŸ—‘ï¸ FORCE CLEARING Chrome browsing data...")
            
            # Method 1: Clear cache/cookies only (keep profile intact)
            self.clear_cache_cookies_only()
            
            print("   â„¹ï¸ Profile directory preserved for login/extensions")
            
            # Method 2: Clear system Chrome cache (if accessible)
            try:
                username = getpass.getuser()
                
                chrome_cache_paths = [
                    f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cache",
                    f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Code Cache",
                    f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\GPUCache",
                ]
                
                print(f"   ðŸ” Checking {len(chrome_cache_paths)} cache directories for user: {username}")
                
                for cache_path in chrome_cache_paths:
                    if os.path.exists(cache_path):
                        try:
                            shutil.rmtree(cache_path, ignore_errors=True)
                            print(f"   âœ… Cleared cache: {cache_path}")
                        except Exception as cache_error:
                            print(f"   âš ï¸ Cache in use: {cache_path} - {cache_error}")
                    else:
                        print(f"   â„¹ï¸ Cache not found: {cache_path}")
                        
            except Exception as e:
                print(f"   âš ï¸ System cache clearing failed: {e}")
            
            print("âœ… Force clear completed")
            return True
            
        except Exception as e:
            print(f"âŒ Force clear error: {e}")
            return False

    def safe_window_operation(self, window, operation_name, operation_func):
        """
        ðŸ›¡ï¸ NEW: Safe window operation with Windows error handling
        """
        try:
            print(f"   ðŸ”„ Attempting {operation_name}...")
            operation_func()
            print(f"   âœ… {operation_name} completed")
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if ("error code from windows: 0" in error_msg or 
                "operation completed successfully" in error_msg or
                "the operation completed successfully" in error_msg):
                print(f"   âœ… {operation_name} completed (Windows quirk - success reported as error)")
                return True
            else:
                print(f"   âš ï¸ {operation_name} warning: {e}")
                print(f"   ðŸ’¡ Continuing anyway - not critical for workflow")
                return False

    def safe_activate_window(self, window):
        """ðŸ›¡ï¸ Safe window activation"""
        return self.safe_window_operation(window, "window activation", lambda: window.activate())

    def safe_maximize_window(self, window):
        """ðŸ›¡ï¸ Safe window maximization"""
        return self.safe_window_operation(window, "window maximization", lambda: window.maximize())

    def wait_for_complete_game_readiness(self, url):
        """
        ðŸ”„ CONTINUOUS MONITORING: Wait for both page + game ready with configurable timeout
        """
        try:
            # Get configuration from config.json
            continuous_config = self.app_config.get('continuous_monitoring', {})
            page_config = self.app_config.get('page_verification', {})
            game_config = self.app_config.get('game_load_verification', {})
            
            # Get timeouts and intervals
            max_timeout = continuous_config.get('max_wait_time_seconds', 120)
            check_interval = continuous_config.get('check_interval_seconds', 10)
            early_exit = continuous_config.get('early_exit_on_success', True)
            
            print(f"\nðŸ”„ CONTINUOUS GAME READINESS MONITORING")
            print("="*70)
            print(f"   â° Maximum timeout: {max_timeout}s")
            print(f"   ðŸ”„ Check interval: {check_interval}s")
            print(f"   ðŸŽ¯ Target: Both page AND game verification success")
            print(f"   ðŸš€ Early exit: {'âœ… Enabled' if early_exit else 'âŒ Disabled'}")
            print("="*70)
            
            start_time = time.time()
            check_count = 0
            
            while True:
                check_count += 1
                elapsed = time.time() - start_time
                remaining = max_timeout - elapsed
                
                if elapsed >= max_timeout:
                    print(f"â° TIMEOUT REACHED after {max_timeout}s")
                    print(f"   ðŸ“Š Total checks performed: {check_count}")
                    return False
                
                print(f"\nðŸ” CHECK #{check_count} (Elapsed: {elapsed:.1f}s, Remaining: {remaining:.1f}s)")
                print("-" * 50)
                
                # Phase 1: Page verification
                print("ðŸ“„ Checking page load status...")
                page_ready = self.verify_page_fully_loaded_lightweight(url)
                page_status = "âœ… READY" if page_ready else "â³ NOT READY"
                print(f"   ðŸ“„ Page status: {page_status}")
                
                # Phase 2: Game verification  
                print("ðŸŽ® Checking game components status...")
                game_ready = self.verify_game_components_ready_lightweight()
                game_status = "âœ… READY" if game_ready else "â³ NOT READY"
                print(f"   ðŸŽ® Game status: {game_status}")
                
                # Check if both ready
                both_ready = page_ready and game_ready
                
                if both_ready and early_exit:
                    print(f"\nðŸŽ‰ SUCCESS! Both page and game ready after {elapsed:.1f}s")
                    print(f"   ðŸ“Š Checks performed: {check_count}")
                    print(f"   ðŸŽ¯ Early exit - no need to wait full timeout")
                    print("="*70)
                    return True
                
                # Log current status
                print(f"   ðŸ“Š Combined status: Page({page_status}) + Game({game_status}) = {'âœ… READY' if both_ready else 'â³ WAITING'}")
                
                # Calculate next check time
                if remaining >= check_interval:
                    print(f"   â³ Next check in {check_interval}s...")
                    time.sleep(check_interval)
                else:
                    print(f"   â° Less than {check_interval}s remaining, final check...")
                    time.sleep(remaining)
                    break
            
            # Final timeout
            print(f"\nâ° TIMEOUT: Max wait time ({max_timeout}s) exceeded")
            print(f"   ðŸ“Š Total checks: {check_count}")
            print(f"   âš ï¸ Will proceed with Run button detection anyway")
            return False
            
        except Exception as e:
            print(f"âŒ Error in continuous monitoring: {e}")
            return False
        
    def navigate_and_prepare_game(self, url):
        """Navigate to game URL using Chrome debug mode with Continuous Monitoring + Extension Support"""
        try:
            # [PHASE 2] Use structured config for settings if available
            if self.config:
                print("   -> Using structured config for browser settings.")
                _debugging_port = self.config.chrome.debugging_port
                _user_data_dir = self.config.chrome.user_data_dir
                _chrome_paths = self.config.chrome.executable_paths
                _page_load_wait = self.config.timing.page_load_wait
            else:
                print("   -> Fallback: Using self.player for browser settings.")
                _debugging_port = self.debugging_port
                _user_data_dir = self.user_data_dir
                _chrome_paths = self.chrome_paths
                _page_load_wait = self.page_load_wait

            print(f"ðŸŒ  Opening game URL with Chrome debug session: {url}")
            print(f"ðŸ”§ Service Mode: Enhanced error handling enabled")
            print(f"ðŸ›¡ï¸  Windows API quirks: Will handle 'success as error' cases")
            print(f"ðŸ”Œ Extension Support: Optimized for extension compatibility")
            
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
                print("âš ï¸  uBlock Origin not found in 'extensions' folder - continuing without AdBlocker")
            else:
                print(f"âœ… Found AdBlocker: {ublock_path}")
            
            # Find Chrome executable
            chrome_exe = None
            for path in self.chrome_paths:
                if os.path.exists(path): 
                    chrome_exe = path
                    break
                    
            if not chrome_exe:
                print("â Œ CRITICAL ERROR: Google Chrome not found")
                print("   ðŸ’¡ Please install Google Chrome or check chrome_paths in config.json")
                return False
            
            # Build Chrome command with debug mode + EXTENSION-FRIENDLY cleanup flags
            chrome_cmd = [
                chrome_exe, 
                f"--remote-debugging-port={self.debugging_port}", 
                f"--user-data-dir={os.path.join(os.getcwd(), self.user_data_dir)}",
                "--no-first-run",
                "--incognito", 
                "--window-size=1280,720", 
                "--remote-allow-origins=*",
                
                # ðŸ†• EXTENSION-SAFE CLEANUP FLAGS
                "--clear-token-service",                    # âœ… Safe: chá»‰ clear auth tokens
                "--clear-key-system-properties",           # âœ… Safe: chá»‰ clear key system  
                "--no-default-browser-check",              # âœ… Safe: khÃ´ng áº£nh hÆ°á»Ÿng extensions
                "--disable-client-side-phishing-detection", # âœ… Safe: khÃ´ng liÃªn quan extensions
                
                # ðŸ†• POPUP & RESTORE FLAGS (EXTENSION-FRIENDLY)
                "--disable-popup-blocking",                # âœ… GOOD: cho phÃ©p popups (tá»‘t cho extensions)
                "--disable-prompt-on-repost",             # âœ… Safe: chá»‰ táº¯t prompt
                "--disable-hang-monitor",                 # âœ… Safe: chá»‰ táº¯t hang detection
                "--disable-session-crashed-bubble",       # âœ… Safe: táº¯t crash bubble
                "--disable-infobars",                     # âœ… Safe: táº¯t info bars
                "--no-recovery-component",                # âœ… Safe: táº¯t crash recovery
                
                # ðŸ†• PERFORMANCE FLAGS (EXTENSION-COMPATIBLE)
                "--disable-backgrounding-occluded-windows", # âœ… Safe: khÃ´ng áº£nh hÆ°á»Ÿng extension background
                "--disable-renderer-backgrounding",         # âœ… Safe: khÃ´ng throttle renderer
                "--disable-background-timer-throttling",    # âœ… Safe: khÃ´ng throttle timers
                
                # ðŸ†• EXTENSION SUPPORT FLAGS
                "--enable-extensions",                     # âœ… EXPLICIT: Ä‘áº£m báº£o extensions enabled
                "--load-plugins",                          # âœ… SUPPORT: há»— trá»£ plugins náº¿u cáº§n
                
                url
            ]

            # ðŸ†• ENHANCED EXTENSION LOADING
            extension_paths = []

            # Add uBlock Origin if available
            if ublock_path:
                extension_paths.append(ublock_path)
                print(f"   ðŸ“¦ Adding uBlock Origin: {ublock_path}")

            # ðŸ†• AUTO-DETECT: TÃ¬m cÃ¡c extensions khÃ¡c trong profile (náº¿u cÃ³)
            try:
                profile_extensions_dir = os.path.join(os.getcwd(), self.user_data_dir, "Default", "Extensions")
                if os.path.exists(profile_extensions_dir):
                    existing_extensions = os.listdir(profile_extensions_dir)
                    print(f"   ðŸ“¦ Found {len(existing_extensions)} existing extensions in profile")
                    
                    # Log existing extensions (khÃ´ng load thÃªm, chá»‰ thÃ´ng bÃ¡o)
                    for ext_id in existing_extensions[:3]:  # Show first 3
                        print(f"      - Extension ID: {ext_id}")
                else:
                    print("   ðŸ“¦ No existing extensions directory found (fresh profile)")
                    
            except Exception as e:
                print(f"   âš ï¸  Could not check existing extensions: {e}")

            # Add all extension paths to Chrome command
            if extension_paths:
                extensions_arg = ",".join(extension_paths)
                chrome_cmd.insert(-1, f"--load-extension={extensions_arg}")
                print(f"   âœ… Loading {len(extension_paths)} extensions: {extensions_arg}")
            else:
                print("   â„¹ï¸  No additional extensions to load")

            # ðŸ†• LOG Chrome flags for verification
            print("ðŸ”§ Chrome startup flags applied:")
            print("   âœ… Safe cleanup flags: session clearing, popup blocking")  
            print("   âœ… Restore popup disabled: no 'Chrome didn't shut down correctly'")
            print("   âœ… Game-friendly: JavaScript, networking, timers enabled")
            print("   âœ… Automation-optimized: no background throttling")
            print("   âœ… Extension-compatible: background mode preserved")
            print(f"   ðŸ“Š Total flags: {len([f for f in chrome_cmd if f.startswith('--')])} flags")
            
            print("ðŸ”§ Launching Chrome with debug mode and AdBlocker...")
            self.chrome_process = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            print(f"â ³ Waiting {self.page_load_wait}s for Chrome and AdBlocker to initialize...")
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
                print("â Œ CRITICAL ERROR: Could not find the game window")
                print("   ðŸ’¡ Chrome may have failed to start or game page didn't load")
                return False
                
            print(f"ðŸŽ¯ Found game window: '{game_window.title}'. Activating...")

            # ðŸ”§ ENHANCED: Safe window operations vá»›i error handling
            try:
                print("   ðŸ”„ Attempting to activate window...")
                game_window.activate()
                print("   âœ… Window activation completed")
                time.sleep(0.5)
            except Exception as e:
                # Handle the "Error code from Windows: 0" issue
                error_msg = str(e).lower()
                if "error code from windows: 0" in error_msg or "operation completed successfully" in error_msg:
                    print("   âœ… Window activation completed (Windows quirk - success reported as error)")
                else:
                    print(f"   âš ï¸  Window activation warning: {e}")

            try:
                print("   ðŸ”„ Attempting to maximize window...")
                game_window.maximize()
                print("   âœ… Window maximization completed")
                time.sleep(2)
            except Exception as e:
                # Handle the "Error code from Windows: 0" issue
                error_msg = str(e).lower()
                if "error code from windows: 0" in error_msg or "operation completed successfully" in error_msg:
                    print("   âœ… Window maximization completed (Windows quirk - success reported as error)")
                else:
                    print(f"   âš ï¸  Window maximization warning: {e}")
                time.sleep(2)

            # [PHASE 1] Publish browser_ready event after window setup
            if self.event_bus:
                self.event_bus.publish(
                    'browser_ready',
                    {'url': url, 'debugging_port': self.debugging_port},
                    source="BrowserManager"
                )

            # ðŸ”§ NEW: Additional focus ensuring for service mode
            print("ðŸŽ¯ Ensuring enhanced focus for service mode...")
            self.ensure_game_focus_light()  # ðŸ†• Use SAFE focus method

            print("âœ… Window setup completed - ready for game operations")

            # ðŸ”§ WINDOW VERIFICATION: Ensure window is responsive after operations
            try:
                # Test if window is responsive
                window_title = game_window.title
                window_rect = game_window.rect if hasattr(game_window, 'rect') else None
                print(f"âœ… Window verification: Title='{window_title}', Responsive=True")
            except Exception as e:
                print(f"âš ï¸  Window verification warning: {e}")
                print("ðŸ’¡ Window may still be functional - continuing")

            print("ðŸŽ¯ Proceeding with game setup...")

            # ================================================================
            # ðŸ”„ NEW: CONTINUOUS MONITORING SYSTEM (REPLACES SMART VERIFICATION)
            # ================================================================
            print(f"\nðŸ”„ STARTING CONTINUOUS MONITORING SYSTEM")
            print("="*70)
            
            both_ready = self.wait_for_complete_game_readiness(url)
            
            if both_ready:
                print("âœ… CONTINUOUS MONITORING SUCCESS: Both page and game fully ready!")
                print("ðŸŽ¯ Proceeding immediately to Run button detection...")
                time.sleep(self.stabilization_wait)  # Brief stabilization
            else:
                print("âš ï¸ CONTINUOUS MONITORING TIMEOUT: Proceeding with limited reliability...")
                print("ðŸ’¡ Run button detection may have lower success rate")
                time.sleep(3)  # Brief pause before proceeding
            
            print("ðŸŽ¯ Continuous monitoring completed - starting Run button detection...")
            print("="*70)
            
            # ================================================================
            # ðŸ”§ ENHANCED: CDP-Only Run button detection (NO REFRESH POLICY)
            # ================================================================

            run_button_clicked = False
            max_retries = self.game_config.get('run_button_max_retries', 3)

            for attempt in range(1, max_retries + 1):
                print(f"\n--- ðŸ—¯ï¸ Finding 'Run' Button via CDP-ONLY - Attempt {attempt}/{max_retries} ---")
                
                if attempt > 1:
                    # âœ… NO MORE REFRESH - Just wait more patiently
                    print("   -> Action: Waiting patiently for Run button (NO PAGE REFRESH)")
                    continuous_config = self.app_config.get('continuous_monitoring', {})
                    patient_wait = continuous_config.get('patient_retry_wait_seconds', 30)
                    print(f"   -> â³ Patient wait: {patient_wait}s for dynamic loading...")
                    time.sleep(patient_wait)
                else:
                    # First attempt - using continuous monitoring results
                    print("   -> Using continuous monitoring results (optimal timing)")
                
                # ðŸ”§ SERVICE MODE FIX: Ensure window activation before clicking Run button
                print("   -> ðŸ”§ Ensuring window activation for service mode...")
                try:
                    possible_windows = gw.getWindowsWithTitle('cocrea.world')
                    if not possible_windows: 
                        possible_windows = gw.getWindowsWithTitle('Chrome')
                    if possible_windows:
                        target_window = possible_windows[0]
                        target_window.activate()
                        time.sleep(0.5)
                        print(f"   -> âœ… Window re-activated: '{target_window.title}'")
                    else:
                        print("   -> âš ï¸ Could not find window to activate")
                except Exception as e:
                    print(f"   -> âš ï¸ Window activation failed: {e}")
                
                print("   -> ðŸ” Searching for 'Run' button via CDP-ONLY...")

                # ðŸ”§ SERVICE MODE FIX: Final activation before detector
                print("   -> ðŸ”§ Final window activation before Run button detection...")
                try:
                    possible_windows = gw.getWindowsWithTitle('cocrea.world')
                    if not possible_windows: 
                        possible_windows = gw.getWindowsWithTitle('Chrome')
                    if possible_windows:
                        target_window = possible_windows[0]
                        target_window.activate()
                        target_window.restore()
                        target_window.maximize()
                        time.sleep(1.0)  # Critical wait
                        print(f"   -> âœ… Final activation complete: '{target_window.title}'")
                    else:
                        print("   -> âš ï¸ Could not find window for final activation")
                except Exception as e:
                    print(f"   -> âš ï¸ Final activation failed: {e}")

                # ðŸš€ CDP-ONLY Run button detection
                if self._click_run_button_cdp_only():
                    print(f"ðŸ” Verifying Run button click had effect...")
                    
                    # Wait for game to start loading
                    time.sleep(3)
                    
                    # Verify game actually started (look for game elements)
                    ws_url = self._get_websocket_url()
                    if ws_url:  # âœ… Kiá»ƒm tra None trÆ°á»›c khi sá»­ dá»¥ng
                        ws_verify = websocket.create_connection(ws_url, timeout=10)
                        verify_response = self._send_cdp_command(ws_verify, {
                            "method": "Runtime.evaluate",
                            "params": {"expression": "!!document.querySelector('.style_gandi-player-wrapper__WCs0P, canvas')", "returnByValue": True}
                        })
                        ws_verify.close()
                        
                        game_loaded = False
                        if verify_response and 'result' in verify_response:
                            game_loaded = verify_response.get('result', {}).get('result', {}).get('value', False)
                    else:
                        print("âš ï¸ WebSocket not available for verification")
                        game_loaded = True  # Assume success if can't verify
                    
                    if game_loaded:
                        print(f"âœ… SUCCESS: Run button effect VERIFIED on attempt {attempt}.")
                        run_button_clicked = True
                        break
                    else:
                        print(f"âŒ Run button clicked but no game loading detected")
                        print(f"ðŸ”„ Treating as failure - will retry with patience")
                else:
                    print(f"âŒ FAILED: Run button not found via CDP on attempt {attempt}.")
                    if attempt < max_retries:
                        print("   -> Will wait patiently and retry (no refresh policy)")
                    else:
                        print(f"âŒ Setup Error: Could not find 'Run' button after {max_retries} patient attempts.")
                        return False
                        
            if not run_button_clicked: 
                return False
                
            print(f"\nâ³ Waiting {self.player.game_load_wait}s for game to load after clicking Run...")
            time.sleep(self.player.game_load_wait)
            
            # ================================================================
            # ðŸ“º FULLSCREEN BUTTON DETECTION (MODIFIED WITH PATIENT RETRY LOGIC)
            # ================================================================
            fullscreen_clicked = False
            max_retries = 3

            for attempt in range(1, max_retries + 1):
                print(f"\n--- ðŸ“º Finding 'Fullscreen' Button via CDP-ONLY - Attempt {attempt}/{max_retries} ---")

                if attempt > 1:
                    print("   -> Action: Patient wait for Fullscreen button (NO PAGE REFRESH)")
                    continuous_config = self.app_config.get('continuous_monitoring', {})
                    patient_wait = continuous_config.get('patient_retry_wait_seconds', 30)
                    print(f"   -> â³ Patient wait: {patient_wait}s for fullscreen button...")
                    time.sleep(patient_wait)

                    # Re-maximize window after patient wait, no reload
                    print("   -> ðŸ”§ Re-activating window after patient wait...")
                    try:
                        possible_windows = gw.getWindowsWithTitle('cocrea.world')
                        if not possible_windows: 
                            possible_windows = gw.getWindowsWithTitle('Chrome')
                        if possible_windows:
                            target_window = possible_windows[0]
                            target_window.activate()
                            target_window.restore()
                            target_window.maximize()
                            time.sleep(1.0)
                            print(f"   -> âœ… Window re-activated after patient wait")
                    except Exception as e:
                        print(f"   -> âš ï¸ Re-activate failed: {e}")
                    
                    # Wait for fullscreen button to stabilize
                    time.sleep(max(self.stabilization_wait, 8))

                # Attempt to click the fullscreen button
                fullscreen_success = self._click_fullscreen_button_cdp_only(wait_time=self.fullscreen_wait)
                
                if fullscreen_success:
                    print(f"âœ… SUCCESS: Fullscreen button clicked on attempt {attempt}.")
                    fullscreen_clicked = True
                    time.sleep(2)
                    break # Exit the loop on success
                else:
                    print(f"âŒ FAILED: Fullscreen button not found or clicked on attempt {attempt}.")
                    if attempt == max_retries:
                        print(f"âŒ CRITICAL FAILURE: Could not click Fullscreen button after {max_retries} patient attempts.")
                        self.cleanup_chrome_complete()
                        return False # Fail the entire process after the last attempt

            # Final check after the loop to ensure it was successful
            if not fullscreen_clicked:
                print("âŒ Setup Error: Fullscreen button process failed after all patient retries.")
                return False

            # Press F11 for true fullscreen only after a successful click
            print("âŒ¨ï¸ Pressing F11 for true fullscreen mode...")
            pyautogui.press('f11')
            time.sleep(3)
            
            # ðŸ†• EXTENSION HEALTH CHECK
            print("ðŸ” Verifying extension compatibility after setup...")
            extension_health = self.check_extension_health()

            if extension_health:
                print("âœ… Extensions are working properly with new Chrome flags")
            else:
                print("âš ï¸ Extensions may have issues, but continuing anyway...")
            
            print("âœ… Game setup completed with Continuous Monitoring System + Extension Support. Ready for automation.")
            return True
            
        except Exception as e:
            messagebox.showerror("Setup Error", f"Failed to prepare game: {e}")
            print(f"âŒ Failed to navigate and prepare game: {e}")
            self.cleanup_chrome_complete()
            return False

    def check_extension_health(self):
        """ðŸ” NEW: Check if extensions are loaded and working"""
        try:
            print("ðŸ” Checking extension health...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("   âš ï¸ Cannot check extensions - WebSocket not available")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            # JavaScript to check extensions
            extension_check_js = '''
            (() => {
                const results = {
                    extensionsEnabled: !!chrome.runtime,
                    extensionCount: 0,
                    uBlockDetected: false,
                    extensionsList: []
                };
                
                // Check if extensions API is available
                if (chrome && chrome.runtime) {
                    results.extensionsEnabled = true;
                    
                    // Try to detect uBlock Origin
                    try {
                        if (chrome.runtime.getManifest) {
                            results.currentExtension = chrome.runtime.getManifest().name;
                        }
                    } catch (e) {
                        // This is normal for content scripts
                    }
                    
                    // Check for uBlock by looking for blocked elements
                    const blockedElements = document.querySelectorAll('[style*="display: none"]').length;
                    if (blockedElements > 0) {
                        results.uBlockDetected = true;
                    }
                }
                
                return results;
            })();
            '''
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": extension_check_js, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value', {})
                
                extensions_enabled = result.get('extensionsEnabled', False)
                ublock_detected = result.get('uBlockDetected', False)
                
                print(f"   ðŸ“Š Extension Health Report:")
                print(f"      Extensions API: {'âœ… Available' if extensions_enabled else 'âŒ Not Available'}")
                print(f"      uBlock Origin: {'âœ… Working' if ublock_detected else 'âš ï¸ Not Detected'}")
                
                return extensions_enabled
            else:
                print("   âš ï¸ Could not get extension health data")
                return False
                
        except Exception as e:
            print(f"   âŒ Extension health check error: {e}")
            return False

    def _get_websocket_url(self, max_retries=5, retry_delay=2):
        """
        🔍 FIXED: Get WebSocket URL with retry mechanism and health verification. Automatically invalidates stale URLs.
        """
        import websocket

        # Nếu ws_url đã có, hãy kiểm tra xem nó còn "sống" không
        if self.ws_url:
            try:
                # Cố gắng tạo một kết nối ngắn để kiểm tra
                ws_test = websocket.create_connection(self.ws_url, timeout=3) # Bây giờ sẽ không báo lỗi nữa
                ws_test.close()
                print(f"   ✅ Existing WebSocket URL is still valid: {self.ws_url[:60]}...")
                return self.ws_url
            except Exception:
                # Nếu kết nối thất bại, URL này đã cũ. Xóa nó đi.
                print(f"   ⚠️ Stale WebSocket URL detected. Invalidating and fetching a new one.")
                self.ws_url = None # 🔥 ĐÂY LÀ ĐIỂM QUAN TRỌNG
        
        print(f"🔍 Getting WebSocket URL with retry (max: {max_retries} attempts)...")
        
        for attempt in range(1, max_retries + 1):
            try:
                print(f"   🔄 WebSocket attempt {attempt}/{max_retries}...")
                
                # Check if Chrome debug port is accessible
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                port_result = sock.connect_ex(('localhost', self.debugging_port))
                sock.close()
                
                if port_result != 0:
                    print(f"   ⚠️ Port {self.debugging_port} not accessible, Chrome may not be ready")
                    if attempt < max_retries:
                        print(f"   ⏳ Waiting {retry_delay}s before retry...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        print(f"   🚫 Port {self.debugging_port} still not accessible after {max_retries} attempts")
                        return None
                
                # Port accessible, try to get tabs
                import requests
                response = requests.get(f"http://localhost:{self.debugging_port}/json", timeout=5)
                
                if response.status_code != 200:
                    print(f"   ⚠️ HTTP {response.status_code} from debug endpoint")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        continue
                    else:
                        return None
                
                tabs = response.json()
                print(f"   📋 Found {len(tabs)} tabs from Chrome debug endpoint")
                
                # Find cocrea.world tab
                target_tab = None
                for tab in tabs:
                    if (tab.get('type') == 'page' and 
                        'cocrea.world' in tab.get('url', '').lower()):
                        target_tab = tab
                        print(f"   🎯 Found cocrea.world tab: {tab.get('title', 'Unknown')[:50]}...")
                        break
                
                if not target_tab:
                    print(f"   ⚠️ No cocrea.world tab found in {len(tabs)} tabs")
                    
                    # Log available tabs for debugging
                    for i, tab in enumerate(tabs[:3]):  # Show first 3 tabs
                        url = tab.get('url', 'No URL')[:50]
                        title = tab.get('title', 'No title')[:30]
                        print(f"      Tab {i+1}: {title} - {url}")
                    
                    if attempt < max_retries:
                        print(f"   ⏳ Tab may still be loading, waiting {retry_delay}s...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        print("   🚫 No cocrea.world tab found after all attempts")
                        return None
                
                # Test WebSocket connection
                ws_url = target_tab['webSocketDebuggerUrl']
                print(f"   🔌 Testing WebSocket connection to: {ws_url[:50]}...")
                
                try:
                    import websocket
                    test_ws = websocket.create_connection(ws_url, timeout=5)
                    
                    # Send a simple test command
                    test_command = {
                        "id": 999999,
                        "method": "Runtime.evaluate",
                        "params": {"expression": "1", "returnByValue": True}
                    }
                    test_ws.send(json.dumps(test_command))
                    
                    # Wait for response
                    test_response = test_ws.recv()
                    test_ws.close()
                    
                    response_data = json.loads(test_response)
                    if response_data.get('id') == 999999:
                        self.ws_url = ws_url
                        print(f"   ✅ WebSocket connection verified and functional")
                        return self.ws_url
                    else:
                        print(f"   ⚠️ WebSocket test command failed")
                        
                except Exception as ws_error:
                    print(f"   ⚠️ WebSocket test failed: {ws_error}")
                
                if attempt < max_retries:
                    print(f"   ⏳ WebSocket test failed, waiting {retry_delay}s before retry...")
                    time.sleep(retry_delay)
                
            except Exception as e:
                print(f"   ⚠️ WebSocket URL attempt {attempt} error: {e}")
                if attempt < max_retries:
                    print(f"   ⏳ Waiting {retry_delay}s before retry...")
                    time.sleep(retry_delay)
        
        print(f"🚫 Failed to get functional WebSocket URL after {max_retries} attempts")
        return None

    def verify_cdp_ready(self, max_attempts=3):
        """
        🔍 NEW: Verify CDP is ready and functional before proceeding
        """
        try:
            print(f"🔍 Verifying CDP readiness (max {max_attempts} attempts)...")
            
            for attempt in range(1, max_attempts + 1):
                print(f"   🔄 CDP verification attempt {attempt}/{max_attempts}...")
                
                # Step 1: Get WebSocket URL with retry
                ws_url = self._get_websocket_url()
                if not ws_url:
                    print(f"   ❌ WebSocket URL not available on attempt {attempt}")
                    if attempt < max_attempts:
                        print("   ⏳ Waiting 3s before retry...")
                        time.sleep(3)
                        continue
                    else:
                        return False
                
                # Step 2: Test CDP commands
                try:
                    import websocket
                    ws = websocket.create_connection(ws_url, timeout=10)
                    
                    # Test basic Runtime command
                    test_response = self._send_cdp_command(ws, {
                        "method": "Runtime.evaluate",
                        "params": {"expression": "window.location.href", "returnByValue": True}
                    })
                    
                    if test_response and 'result' in test_response:
                        current_url = test_response.get('result', {}).get('result', {}).get('value', '')
                        print(f"   ✅ CDP test successful - Page: {current_url[:50]}...")
                        
                        # Test if it's the right page
                        if 'cocrea.world' in current_url.lower():
                            ws.close()
                            print(f"   🎯 CDP verified ready on cocrea.world page")
                            return True
                        else:
                            print(f"   ⚠️ CDP working but wrong page: {current_url}")
                    else:
                        print(f"   ⚠️ CDP test command failed - no valid response")
                    
                    ws.close()
                    
                except Exception as cdp_error:
                    print(f"   ❌ CDP test error: {cdp_error}")
                
                if attempt < max_attempts:
                    print("   ⏳ CDP not ready, waiting 5s before retry...")
                    time.sleep(5)
            
            print(f"❌ CDP not ready after {max_attempts} attempts")
            return False
            
        except Exception as e:
            print(f"❌ CDP verification error: {e}")
            return False

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
                except Exception as timeout_e:
                    if "timeout" in str(timeout_e).lower():
                        print("WARNING: WebSocket timeout, continuing to wait...")
                        continue
                    else:
                        raise timeout_e
                    
            print(f"ERROR: CDP command timeout after {max_wait_time}s")
            return None
            
        except Exception as e:
            print(f"ERROR: CDP command error: {e}")
            return None

    def _click_run_button_cdp_only(self):
        """Pure CDP implementation for Run button detection and clicking"""
        try:
            print("ðŸ”§ CDP-ONLY: Finding and clicking Run button via DOM...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âŒ CDP-ONLY: WebSocket not available")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # JavaScript to find and click Run button
            run_button_js = '''
            new Promise((resolve, reject) => {
                try {
                    console.log("ðŸ”§ CDP-ONLY: Searching for Run button in DOM...");
                    
                    // Strategy 1: Specific class from extracted data
                    let runButton = document.querySelector('.style_run-button__VjFSY');
                    let strategy = 'specific_class';
                    
                    // Strategy 2: Material-UI button classes
                    if (!runButton) {
                        const muiSelectors = [
                            'button.MuiButton-contained.MuiButton-containedPrimary',
                            'button[class*="style_run-button"]',
                            'button.MuiButton-sizeLarge'
                        ];
                        
                        for (const selector of muiSelectors) {
                            try {
                                const buttons = document.querySelectorAll(selector);
                                for (const btn of buttons) {
                                    const text = (btn.textContent || '').trim().toLowerCase();
                                    if (text === 'run') {
                                        runButton = btn;
                                        strategy = `mui_selector_${selector}`;
                                        break;
                                    }
                                }
                                if (runButton) break;
                            } catch (e) {
                                console.warn(`Selector failed: ${selector}`);
                            }
                        }
                    }
                    
                    // Strategy 3: Text-based search (broad)
                    if (!runButton) {
                        const allButtons = document.querySelectorAll('button, input[type="submit"], input[type="button"]');
                        for (const btn of allButtons) {
                            const text = (btn.textContent || btn.value || '').trim().toLowerCase();
                            if (text === 'run' || text === 'â–¶' || text === 'play' || text === 'start') {
                                runButton = btn;
                                strategy = `text_based_${text}`;
                                break;
                            }
                        }
                    }
                    
                    // Strategy 4: Context-based search (in game containers)
                    if (!runButton) {
                        const gameContainers = document.querySelectorAll('.style_stage-cover__VITCW, .ccw-stage-wrapper, #stage-canvas-wrapper');
                        for (const container of gameContainers) {
                            const buttons = container.querySelectorAll('button, input[type="submit"]');
                            for (const btn of buttons) {
                                const text = (btn.textContent || btn.value || '').trim().toLowerCase();
                                if (text.includes('run') || text.includes('play') || text.includes('start')) {
                                    runButton = btn;
                                    strategy = 'context_based';
                                    break;
                                }
                            }
                            if (runButton) break;
                        }
                    }
                    
                    if (runButton) {
                        console.log(`ðŸŽ¯ CDP-ONLY: Found Run button via ${strategy}`);
                        console.log(`   Text: "${runButton.textContent || runButton.value || 'No text'}"`);
                        console.log(`   Classes: "${runButton.className || 'No classes'}"`);
                        
                        // Scroll button into view
                        runButton.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        
                        // Wait for scroll to complete
                        setTimeout(() => {
                            try {
                                // Attempt 1: Direct click
                                runButton.click();
                                console.log("âœ… CDP-ONLY: Run button clicked via direct click");
                                resolve({ success: true, method: 'direct_click', strategy: strategy });
                                
                            } catch (clickError) {
                                console.log("âš ï¸ CDP-ONLY: Direct click failed, trying dispatchEvent...");
                                
                                try {
                                    // Attempt 2: Dispatch click event
                                    const clickEvent = new MouseEvent('click', {
                                        view: window,
                                        bubbles: true,
                                        cancelable: true,
                                        button: 0
                                    });
                                    runButton.dispatchEvent(clickEvent);
                                    
                                    console.log("âœ… CDP-ONLY: Run button clicked via dispatchEvent");
                                    resolve({ success: true, method: 'dispatch_event', strategy: strategy });
                                    
                                } catch (dispatchError) {
                                    console.log("âš ï¸ CDP-ONLY: DispatchEvent failed, trying focus + click...");
                                    
                                    try {
                                        // Attempt 3: Focus + click
                                        runButton.focus();
                                        setTimeout(() => {
                                            runButton.click();
                                            console.log("âœ… CDP-ONLY: Run button clicked via focus + click");
                                            resolve({ success: true, method: 'focus_click', strategy: strategy });
                                        }, 200);
                                        
                                    } catch (focusError) {
                                        console.error("âŒ CDP-ONLY: All click methods failed:", focusError);
                                        resolve({ success: false, reason: 'all_click_methods_failed', strategy: strategy });
                                    }
                                }
                            }
                        }, 1000); // Wait 1s for scroll
                        
                    } else {
                        console.log("âŒ CDP-ONLY: Run button not found in DOM");
                        resolve({ success: false, reason: 'button_not_found' });
                    }
                    
                } catch (error) {
                    console.error("âŒ CDP-ONLY: Error finding Run button:", error);
                    reject({ success: false, error: error.toString() });
                }
            });
            '''
            
            # Execute the JavaScript
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": run_button_js, "awaitPromise": True, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')
                if result and result.get('success'):
                    method = result.get('method', 'unknown')
                    strategy = result.get('strategy', 'unknown')
                    print(f"âœ… CDP-ONLY: Run button clicked successfully via {method} (strategy: {strategy})")
                    return True
                else:
                    reason = result.get('reason', 'unknown') if result else 'no_result'
                    strategy = result.get('strategy', 'unknown') if result else 'none'
                    print(f"âŒ CDP-ONLY: Run button click failed - {reason} (strategy attempted: {strategy})")
                    return False
            else:
                print("âŒ CDP-ONLY: Failed to execute Run button JavaScript")
                return False
                
        except Exception as e:
            print(f"âŒ CDP-ONLY: Run button detection error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _click_fullscreen_button_cdp_only(self, wait_time=15):
        """SVG-aware Fullscreen with RELAXED verification"""
        try:
            print("ðŸ”§ CDP-ONLY: SVG-aware Fullscreen with RELAXED verification...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âŒ CDP-ONLY: WebSocket not available")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # Relaxed verification JavaScript
            fullscreen_button_js = f'''
            new Promise((resolve, reject) => {{
                try {{
                    console.log("ðŸ”§ CDP-ONLY: SVG-aware fullscreen with RELAXED verification...");
                    
                    const maxWaitTime = {wait_time * 1000};
                    const startTime = Date.now();
                    
                    function checkFullscreenStates() {{
                        const results = {{
                            browserFullscreen: !!(document.fullscreenElement || 
                                                document.webkitFullscreenElement || 
                                                document.mozFullScreenElement || 
                                                document.msFullscreenElement),
                            windowDimensions: {{
                                innerWidth: window.innerWidth,
                                innerHeight: window.innerHeight,
                                screenWidth: screen.width,
                                screenHeight: screen.height,
                                isMaximized: window.innerWidth === screen.width && window.innerHeight === screen.height
                            }},
                            documentSizes: {{
                                documentWidth: document.documentElement.clientWidth,
                                documentHeight: document.documentElement.clientHeight
                            }},
                            gameContainerStates: {{}}
                        }};
                        
                        // Check game container for fullscreen classes/styles
                        const gameContainers = [
                            '.style_gandi-player-wrapper__WCs0P',
                            '.style_stage-player__0Xy7d',
                            '.ccw-stage-wrapper'
                        ];
                        
                        gameContainers.forEach(selector => {{
                            const container = document.querySelector(selector);
                            if (container) {{
                                const computedStyle = window.getComputedStyle(container);
                                results.gameContainerStates[selector] = {{
                                    position: computedStyle.position,
                                    zIndex: computedStyle.zIndex,
                                    width: container.offsetWidth,
                                    height: container.offsetHeight,
                                    className: container.className,
                                    hasFullscreenClass: container.className.includes('fullscreen')
                                }};
                            }}
                        }});
                        
                        return results;
                    }}
                    
                    function analyzeIconBySVG(iconDiv) {{
                        const svg = iconDiv.querySelector('svg[viewBox="0 0 40 40"]');
                        if (!svg) return {{ type: 'unknown', reason: 'no_svg' }};
                        
                        const paths = svg.querySelectorAll('path');
                        const pathCount = paths.length;
                        const firstPath = paths[0] ? paths[0].getAttribute('d') || '' : '';
                        
                        // Pause: 1 path with pause pattern
                        if (pathCount === 1 && (firstPath.includes('C11.75 5') || firstPath.includes('13.8125'))) {{
                            return {{ type: 'pause', pathCount: pathCount }};
                        }}
                        
                        // Fullscreen: Multiple paths with expand pattern
                        if (pathCount >= 4 && (firstPath.includes('6.01539') || firstPath.includes('5.4546'))) {{
                            return {{ type: 'fullscreen', pathCount: pathCount }};
                        }}
                        
                        // Generic multi-path (likely fullscreen)
                        if (pathCount >= 4) {{
                            return {{ type: 'likely_fullscreen', pathCount: pathCount }};
                        }}
                        
                        return {{ type: 'unknown', pathCount: pathCount }};
                    }}
                    
                    function findFullscreenButton() {{
                        const allIcons = document.querySelectorAll('.style_gandi-player-actions-icon__uoqdH');
                        console.log(`ðŸŽ¯ Analyzing ${{allIcons.length}} action icons...`);
                        
                        for (let i = 0; i < allIcons.length; i++) {{
                            const result = analyzeIconBySVG(allIcons[i]);
                            console.log(`Icon ${{i + 1}}: ${{result.type}} (${{result.pathCount}} paths)`);
                            
                            if (result.type === 'fullscreen' || result.type === 'likely_fullscreen') {{
                                return {{ element: allIcons[i], ...result, index: i }};
                            }}
                        }}
                        
                        return null;
                    }}
                    
                    async function attemptFullscreenClick() {{
                        const candidate = findFullscreenButton();
                        
                        if (!candidate) {{
                            const elapsed = Date.now() - startTime;
                            if (elapsed < maxWaitTime) {{
                                console.log(`ðŸ” Waiting for fullscreen button... (${{Math.round(elapsed / 1000)}}s)`);
                                setTimeout(attemptFullscreenClick, 1000);
                            }} else {{
                                resolve({{ success: false, reason: 'no_fullscreen_button_found' }});
                            }}
                            return;
                        }}
                        
                        const element = candidate.element;
                        console.log(`ðŸŽ¯ Found fullscreen button: ${{candidate.type}} (${{candidate.pathCount}} paths)`);
                        
                        // Get initial states
                        const initialStates = checkFullscreenStates();
                        console.log("ðŸ“Š Initial states:", initialStates);
                        
                        // Scroll and click
                        element.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                        
                        setTimeout(() => {{
                            console.log("ðŸ–±ï¸ Attempting click...");
                            
                            try {{
                                // Simple click first
                                element.click();
                                console.log("âœ… Click executed successfully");
                                
                                // RELAXED verification with multiple checks
                                setTimeout(() => {{
                                    const newStates = checkFullscreenStates();
                                    console.log("ðŸ“Š Post-click states:", newStates);
                                    
                                    // Multiple success criteria (ANY can indicate success)
                                    const successCriteria = {{
                                        browserFullscreen: newStates.browserFullscreen && !initialStates.browserFullscreen,
                                        windowMaximized: newStates.windowDimensions.isMaximized && !initialStates.windowDimensions.isMaximized,
                                        dimensionChange: newStates.windowDimensions.innerWidth !== initialStates.windowDimensions.innerWidth ||
                                                    newStates.windowDimensions.innerHeight !== initialStates.windowDimensions.innerHeight,
                                        gameContainerChange: false // Will check below
                                    }};
                                    
                                    // Check for game container changes
                                    Object.keys(newStates.gameContainerStates).forEach(selector => {{
                                        const initial = initialStates.gameContainerStates[selector];
                                        const current = newStates.gameContainerStates[selector];
                                        if (initial && current) {{
                                            if (current.position !== initial.position || 
                                                current.zIndex !== initial.zIndex ||
                                                current.hasFullscreenClass !== initial.hasFullscreenClass) {{
                                                successCriteria.gameContainerChange = true;
                                            }}
                                        }}
                                    }});
                                    
                                    console.log("âœ… Success criteria analysis:", successCriteria);
                                    
                                    // RELAXED: Any positive indicator = success
                                    const anySuccess = Object.values(successCriteria).some(criteria => criteria === true);
                                    
                                    if (anySuccess) {{
                                        const activeCriteria = Object.keys(successCriteria).filter(key => successCriteria[key]);
                                        console.log(`ðŸŽ‰ RELAXED SUCCESS! Criteria met: ${{activeCriteria.join(', ')}}`);
                                        
                                        resolve({{
                                            success: true,
                                            method: 'direct_click',
                                            candidate: candidate.type,
                                            pathCount: candidate.pathCount,
                                            verified: true,
                                            successCriteria: activeCriteria,
                                            states: {{ initial: initialStates, final: newStates }}
                                        }});
                                    }} else {{
                                        console.log("âš ï¸ No clear success indicators, but click was executed");
                                        console.log("ðŸ¤” Assuming success due to successful click execution");
                                        
                                        // VERY RELAXED: If click executed without error, assume success
                                        resolve({{
                                            success: true,
                                            method: 'direct_click_assumed',
                                            candidate: candidate.type,
                                            pathCount: candidate.pathCount,
                                            verified: false,
                                            assumption: 'click_executed_successfully',
                                            states: {{ initial: initialStates, final: newStates }}
                                        }});
                                    }}
                                    
                                }}, 2000); // Wait 2s for effects to take place
                                
                            }} catch (clickError) {{
                                console.log(`âŒ Click failed: ${{clickError.message}}`);
                                
                                // Try backup method
                                try {{
                                    console.log("ðŸ”„ Trying backup click method...");
                                    const rect = element.getBoundingClientRect();
                                    const clickEvent = new MouseEvent('click', {{
                                        view: window,
                                        bubbles: true,
                                        cancelable: true,
                                        clientX: rect.left + rect.width / 2,
                                        clientY: rect.top + rect.height / 2
                                    }});
                                    element.dispatchEvent(clickEvent);
                                    
                                    console.log("âœ… Backup click executed");
                                    
                                    // Even more relaxed - assume backup worked
                                    setTimeout(() => {{
                                        console.log("ðŸ¤· Backup method completed - assuming success");
                                        resolve({{
                                            success: true,
                                            method: 'backup_click_assumed',
                                            candidate: candidate.type,
                                            pathCount: candidate.pathCount,
                                            verified: false,
                                            assumption: 'backup_click_executed'
                                        }});
                                    }}, 1500);
                                    
                                }} catch (backupError) {{
                                    console.log(`âŒ Backup click also failed: ${{backupError.message}}`);
                                    resolve({{
                                        success: false,
                                        reason: 'both_click_methods_failed',
                                        candidate: candidate.type,
                                        pathCount: candidate.pathCount
                                    }});
                                }}
                            }}
                            
                        }}, 800); // Wait for scroll
                    }}
                    
                    // Start attempt
                    setTimeout(attemptFullscreenClick, 2000);
                    
                }} catch (error) {{
                    console.error("âŒ Relaxed fullscreen error:", error);
                    reject({{ success: false, error: error.toString() }});
                }}
            }});
            '''
            
            # Execute relaxed JavaScript
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": fullscreen_button_js, "awaitPromise": True, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')
                if result and result.get('success'):
                    method = result.get('method', 'unknown')
                    candidate = result.get('candidate', 'unknown')
                    verified = result.get('verified', False)
                    criteria = result.get('successCriteria', [])
                    assumption = result.get('assumption', None)
                    
                    print(f"âœ… CDP-ONLY: RELAXED Fullscreen SUCCESS!")
                    print(f"   ðŸŽ¯ Button: {candidate}")
                    print(f"   ðŸ–±ï¸ Method: {method}")
                    print(f"   âœ… Verified: {verified}")
                    if criteria:
                        print(f"   ðŸ“Š Criteria: {', '.join(criteria)}")
                    if assumption:
                        print(f"   ðŸ¤· Assumption: {assumption}")
                    
                    return True
                else:
                    reason = result.get('reason', 'unknown') if result else 'no_result'
                    print(f"âŒ CDP-ONLY: RELAXED Fullscreen still failed - {reason}")
                    return False
            else:
                print("âŒ CDP-ONLY: Failed to execute relaxed JavaScript")
                return False
                
        except Exception as e:
            print(f"âŒ CDP-ONLY: Relaxed Fullscreen error: {e}")
            return False

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

    def intro_skip_via_cdp(self):
        """ENHANCED: Multi-target Skip intro using CDP with smart detection + blind clicks"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âš ï¸ Could not get WebSocket URL for enhanced intro skipping, using pyautogui fallback")
                return self.intro_skip_via_pyautogui()
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print(f"ðŸ” ENHANCED CDP SKIP INTRO: Multi-target detection + {self.intro_skip_clicks} blind clicks...")
            
            # Phase 1: Smart detection and clicks
            print("ðŸ“ Phase 1: Searching for ALL Skip Intro buttons via CDP...")
            
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
                        
                        print(f"âœ… Found {button_count} Skip Intro buttons (Visible: {visible_count})")
                        
                        # Click ALL buttons found
                        for i, button in enumerate(all_buttons):
                            button_num = i + 1
                            x, y = button['x'], button['y']
                            visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                            costume_name = button.get('currentCostumeName', 'Unknown')
                            
                            print(f"ðŸŽ¯ CDP clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status} - {costume_name}")
                            
                            # Click this specific button via CDP
                            self._simulate_click_via_cdp(ws, x, y)
                            time.sleep(0.8)  # Wait between clicks
                            
                            smart_clicks_performed += 1
                        
                        print(f"âœ… Smart CDP clicks completed: {smart_clicks_performed} buttons clicked!")
                    else:
                        reason = skip_result.get('reason', 'unknown') if skip_result else 'no_result'
                        print(f"â„¹ï¸ No Skip Intro buttons found via CDP - Reason: {reason}")
                
            except Exception as e:
                print(f"âš ï¸ Smart detection failed: {e}, proceeding with blind clicks...")
            
            # Phase 2: ALWAYS perform blind clicks for additional assurance
            if smart_clicks_performed > 0:
                print("ðŸ”„ Phase 2: Performing ADDITIONAL blind CDP clicks for protection...")
                print("   â†’ Ensures intro skipping even if smart clicks had edge cases")
            else:
                print("ðŸ–±ï¸ Phase 2: Performing STANDARD blind CDP clicks (no smart buttons found)...")
            
            # Perform blind clicks via CDP
            for i in range(self.intro_skip_clicks):
                self._simulate_click_via_cdp(ws, 960, 540)
                time.sleep(0.5)
            
            ws.close()
            
            # Summary
            if smart_clicks_performed > 0:
                print("ðŸŽ‰ ENHANCED CDP SKIP INTRO COMPLETED:")
                print(f"   âœ… Smart clicks: {smart_clicks_performed} buttons via CDP")
                print(f"   âœ… Blind clicks: {self.intro_skip_clicks} additional CDP clicks")
                print("   ðŸ›¡ï¸ Dual protection ensures maximum success rate")
            else:
                print("ðŸŽ‰ STANDARD CDP SKIP INTRO COMPLETED:")
                print("   âš ï¸ Smart clicks: N/A (No buttons found)")
                print(f"   âœ… Blind clicks: {self.intro_skip_clicks} CDP clicks")
                print("   ðŸ”„ Standard method used as fallback")
            
            print("âœ… CDP intro skipping completed")
            return True
            
        except Exception as e:
            print(f"âŒ CDP intro skipping failed: {e}, falling back to pyautogui")
            return self.intro_skip_via_pyautogui()

    def intro_skip_via_pyautogui(self):
        """ENHANCED: Multi-target Skip intro using PyAutoGUI with smart detection + blind drags"""
        try:
            print(f"ðŸ” ENHANCED PYAUTOGUI SKIP INTRO: Multi-target detection + {self.intro_skip_clicks} wake-up drags...")
            
            # Phase 1: Try smart detection via WebSocket if available
            print("ðŸ“ Phase 1: Attempting smart detection via WebSocket...")
            
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
                            
                            print(f"âœ… Found {button_count} Skip Intro buttons via WebSocket")
                            
                            # Click ALL buttons found using PyAutoGUI
                            for i, button in enumerate(all_buttons):
                                button_num = i + 1
                                x, y = button['x'], button['y']
                                visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                                
                                print(f"ðŸŽ¯ PyAutoGUI clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status}")
                                
                                # Click this specific button via PyAutoGUI
                                pyautogui.moveTo(x, y, duration=0.2)
                                pyautogui.click(button='left')
                                time.sleep(0.8)  # Wait between clicks
                                
                                smart_clicks_performed += 1
                            
                            print(f"âœ… Smart PyAutoGUI clicks completed: {smart_clicks_performed} buttons clicked!")
                        else:
                            print("â„¹ï¸ No Skip Intro buttons found via WebSocket")
                    
                    ws.close()
                else:
                    print("âš ï¸ WebSocket not available for smart detection")
                    
            except Exception as e:
                print(f"âš ï¸ Smart detection via WebSocket failed: {e}")
            
            # Phase 2: ALWAYS perform aggressive wake-up drags
            if smart_clicks_performed > 0:
                print("ðŸ”„ Phase 2: Performing ADDITIONAL wake-up drags for protection...")
                print("   â†’ Ensures intro skipping even if smart clicks had edge cases")
            else:
                print("âš¡ Phase 2: Performing AGGRESSIVE wake-up drags (no smart buttons found)...")
            
            # Aggressive wake-up drags using existing logic
            print(f"âš¡ AGGRESSIVE INTRO SKIPPING: Performing {self.intro_skip_clicks} 'wake-up drags' over 5 seconds...")
            screen_width, screen_height = pyautogui.size()
            center_x, center_y = screen_width // 2, screen_height // 2
            total_duration = 5.0
            num_drags = self.intro_skip_clicks
            sleep_between_drags = total_duration / num_drags
            
            for i in range(num_drags):
                if self.player.should_stop: 
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
                print("ðŸŽ‰ ENHANCED PYAUTOGUI SKIP INTRO COMPLETED:")
                print(f"   âœ… Smart clicks: {smart_clicks_performed} buttons via PyAutoGUI")
                print(f"   âœ… Wake-up drags: {self.intro_skip_clicks} aggressive drags")
                print("   ðŸ›¡ï¸ Dual protection ensures maximum success rate")
            else:
                print("ðŸŽ‰ AGGRESSIVE PYAUTOGUI SKIP INTRO COMPLETED:")
                print("   âš ï¸ Smart clicks: N/A (No buttons found)")
                print(f"   âœ… Wake-up drags: {self.intro_skip_clicks} aggressive drags")
                print("   âš¡ Aggressive method used as fallback")
            
            print("âœ… Aggressive intro skipping sequence completed.")
            return True
            
        except Exception as e:
            print(f"âŒ PyAutoGUI intro skipping failed: {e}")
            return False

    def ensure_game_focus(self):
        """ðŸ”§ ENHANCED: Ensure game focus vá»›i service mode support - SAFE CLICK VERSION"""
        try:
            print("ðŸŽ¯ Ensuring game focus for service mode...")
            
            # Phase 1: Find vÃ  activate Chrome window
            chrome_windows = gw.getWindowsWithTitle('cocrea.world')
            if not chrome_windows:
                chrome_windows = gw.getWindowsWithTitle('Chrome')
            
            if chrome_windows:
                target_window = chrome_windows[0]
                print(f"   ðŸ” Found window: {target_window.title[:50]}...")
                
                try:
                    # Activate window vá»›i error handling
                    target_window.activate()
                    time.sleep(0.3)
                    
                    # Bring to foreground
                    target_window.restore()  # Ensure not minimized
                    time.sleep(0.2)
                    
                    print("   âœ… Window activated successfully")
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    if "error code from windows: 0" in error_msg:
                        print("   âœ… Window activated (Windows quirk)")
                    else:
                        print(f"   âš ï¸ Window activation warning: {e}")
            
            # ðŸ†• Phase 2: SAFE MOUSE FOCUS - TRÃNH CLICK VÃ€O SLOT
            print("   ðŸŽ¯ Phase 2: Safe mouse focus (avoiding slots/icons)...")
            
            # ðŸ†• SAFE ZONE: Click vÃ o gÃ³c trÃªn bÃªn trÃ¡i (title bar area)
            # Tá»a Ä‘á»™ nÃ y sáº½ click vÃ o title bar cá»§a browser, khÃ´ng pháº£i game area
            safe_x = 20  # GÃ³c trÃ¡i, trÃ¡nh slots á»Ÿ giá»¯a
            safe_y = 200   # Title bar area, trÃ¡nh game canvas
            
            print(f"   ðŸ›¡ï¸ Using SAFE click position: ({safe_x}, {safe_y}) - Title bar area")
            pyautogui.moveTo(safe_x, safe_y, duration=0.2)
            time.sleep(0.1)
            
            # Click vÃ o title bar Ä‘á»ƒ focus
            pyautogui.click(safe_x, safe_y)
            time.sleep(0.2)
            
            # ðŸ†• Phase 3: Move mouse to NEUTRAL position (bottom right corner)
            print("   ðŸŽ¯ Phase 3: Moving mouse to neutral position...")
            screen_width, screen_height = pyautogui.size()
            neutral_x = screen_width - 100  # GÃ³c pháº£i dÆ°á»›i
            neutral_y = screen_height - 100
            
            pyautogui.moveTo(neutral_x, neutral_y, duration=0.3)
            print(f"   âœ… Mouse moved to neutral corner: ({neutral_x}, {neutral_y})")
            
            print("   âœ… Game focus ensured with SAFE method (no slot interference)")
            return True
            
        except Exception as e:
            print(f"   âŒ Enhanced focus error: {e}")
            return False

    def ensure_game_focus_light(self):
        """ðŸª¶ LIGHT FOCUS: Chá»‰ activate window, KHÃ”NG click (an toÃ n nháº¥t)"""
        try:
            print("ðŸª¶ Light focus: Window activation only...")
            
            # Chá»‰ activate window, khÃ´ng click gÃ¬ cáº£
            chrome_windows = gw.getWindowsWithTitle('cocrea.world')
            if not chrome_windows:
                chrome_windows = gw.getWindowsWithTitle('Chrome')
            
            if chrome_windows:
                target_window = chrome_windows[0]
                try:
                    target_window.activate()
                    target_window.restore()
                    time.sleep(0.3)
                    print("   âœ… Light focus completed - no clicks performed")
                    return True
                except Exception as e:
                    error_msg = str(e).lower()
                    if "error code from windows: 0" in error_msg:
                        print("   âœ… Light focus completed (Windows quirk)")
                        return True
                    else:
                        print(f"   âš ï¸ Light focus warning: {e}")
                        return False
            else:
                print("   âš ï¸ No window found for light focus")
                return False
                
        except Exception as e:
            print(f"   âŒ Light focus error: {e}")
            return False

    def _ensure_focus_before_operation(self, operation_name="operation", focus_type="safe"):
        """ðŸ”§ NEW: Äáº£m báº£o focus trÆ°á»›c má»i mouse operation vá»›i tÃ¹y chá»n loáº¡i focus"""
        try:
            print(f"ðŸŽ¯ Ensuring focus before {operation_name}...")
            
            # Chá»n loáº¡i focus dá»±a trÃªn tham sá»‘
            if focus_type == "light":
                # Chá»‰ activate window, khÃ´ng click (an toÃ n nháº¥t)
                success = self.ensure_game_focus_light()
            elif focus_type == "safe":
                # Activate + click an toÃ n (trÃ¡nh slots)
                success = self.ensure_game_focus()
            else:
                # Default to safe
                success = self.ensure_game_focus()
            
            if success:
                print(f"   âœ… Focus ready for {operation_name} (type: {focus_type})")
            else:
                print(f"   âš ï¸ Focus uncertain for {operation_name}, continuing anyway")
            
            return success
            
        except Exception as e:
            print(f"   âŒ Focus check error for {operation_name}: {e}")
            return False

    def restart_game_to_initial_state(self, use_cdp=True):
        """
        THực hiện hành động khởi động lại game (nhấp Re-Run, skip intro).
        PHẦN XÁC THỰC ĐÃ ĐƯỢC CHUYỂN RA NGOÀI.
        """
        try:
            print(f"\nACTION: GAME RESTART TO INITIAL STATE")
            print("="*60)
            print("   Method: Click Re-Run button + Skip intro sequence")
            
            # Ensure window focus before restart
            self._ensure_focus_before_operation("game restart", focus_type="light")
            
            # Click Re-Run button at fixed coordinates
            print("   Step 1: Clicking Re-Run button...")
            # (Logic click Re-run không thay đổi)
            if use_cdp:
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    self._simulate_click_via_cdp(ws, 40, 75)
                    ws.close()
                    print("   Re-Run clicked via CDP")
                else:
                    pyautogui.click(40, 75)
                    print("   Re-Run clicked via PyAutoGUI")
            else:
                pyautogui.click(40, 75)
                print("   Re-Run clicked via PyAutoGUI")
            
            # Wait for game restart process
            print("   Waiting for game restart process (3s)...")
            time.sleep(3.0)
            
            # Skip intro sequence
            print("   Step 2: Skipping intro sequence...")
            if use_cdp:
                intro_success = self.intro_skip_via_cdp()
            else:
                intro_success = self.intro_skip_via_pyautogui()
            
            if intro_success:
                print("   Intro skip completed")
            else:
                print("   Intro skip warning, continuing anyway...")
            
            # Additional stabilization wait
            print("   Game restart stabilization (2s)...")
            time.sleep(2.0)
            
            print("GAME RESTART ACTION COMPLETED!")
            print("="*60)
            return True
            
        except Exception as e:
            print(f"Game restart action error: {e}")
            print("Restart action may have failed.")
            return False                                    
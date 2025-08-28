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
import requests
import websocket
import psutil
import pygetwindow as gw
import shutil
import getpass
import socket
import traceback

class BrowserManager:
    def __init__(self, app_config):
        """
        Khởi tạo BrowserManager với cấu hình ứng dụng.
        """
        print("🚀 BrowserManager initialized.")
        self.app_config = app_config
        
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
                console.log("ðŸ”  [BROWSER MANAGER] Searching for ALL Skip Intro buttons...");
                
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
                    console.log("â Œ [BROWSER MANAGER] VM not found - cannot search for Skip Intro buttons");
                    resolve({ found: false, count: 0, buttons: [], reason: "vm_not_found" });
                    return;
                }
                
                const allTargets = vm.runtime.targets.map(t => t);
                console.log(`ðŸŽ¯ [BROWSER MANAGER] Total targets found: ${allTargets.length}`);
                
                const introTargets = allTargets.filter(target => {
                    if (target.sprite.name !== "Intro") return false;
                    const hasSkipCostume = target.sprite.costumes.some(costume => costume.name === "Skip");
                    return hasSkipCostume;
                });
                
                console.log(`ðŸ”  [BROWSER MANAGER] Found ${introTargets.length} 'Intro' targets with 'Skip' costume`);
                
                if (introTargets.length === 0) {
                    console.log("â„¹ï¸  [BROWSER MANAGER] No Skip Intro buttons found");
                    resolve({ found: false, count: 0, buttons: [], reason: "target_not_found" });
                    return;
                }
                
                const skipIntroButtons = [];
                introTargets.forEach((target, index) => {
                    console.log(`\\nðŸŽ¯ [BROWSER MANAGER] Processing Intro Target ${index + 1}/${introTargets.length}:`);
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
                        console.log(`   âœ… [BROWSER MANAGER] Converted to pixel: (${pixelCoords.x}, ${pixelCoords.y}) - ${target.visible ? 'VISIBLE' : 'HIDDEN'}`);
                    }
                });
                
                const visibleButtons = skipIntroButtons.filter(btn => btn.visible);
                console.log(`\\nðŸ“Š [BROWSER MANAGER] BUTTONS SUMMARY: Total: ${skipIntroButtons.length}, Visible: ${visibleButtons.length}`);
                
                resolve({
                    found: skipIntroButtons.length > 0,
                    count: skipIntroButtons.length,
                    buttons: skipIntroButtons,
                    visibleCount: visibleButtons.length,
                    reason: skipIntroButtons.length > 0 ? "success" : "no_valid_buttons"
                });
            } catch(e) { 
                console.error("â Œ [BROWSER MANAGER] Error searching for Skip Intro buttons:", e);
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
            if not self.page_verification_enabled:
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
            max_retries = self.page_verification_retries
            
            for attempt in range(1, max_retries + 1):
                if self.should_stop:
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
                if elapsed > self.page_verification_timeout:
                    print(f"âš ï¸ Page verification timeout ({self.page_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with image detection...")
                    return True  # Don't fail completely, proceed with image detection
                
                # Wait before next attempt
                if attempt < max_retries:
                    print(f"   â³ Waiting {self.page_verification_interval}s before next check...")
                    time.sleep(self.page_verification_interval)
            
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
            if not self.game_verification_enabled:
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
                
                if ({str(self.check_react_components).lower()}) {{
                    checkReactComponents();
                }}
                
                // ðŸ†• ENHANCED: More comprehensive Run button detection
                if ({str(self.check_run_button_dom).lower()}) {{
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
                const gameContainersFound = results.gameContainers.length >= {self.min_containers_required};
                const reactReady = !{str(self.check_react_components).lower()} || results.reactComponents.found;
                const runButtonReady = !{str(self.check_run_button_dom).lower()} || results.runButtonInDOM;
                
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
                if self.should_stop:
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
                    
                    if self.check_react_components:
                        react_found = react_components.get('found', False)
                        react_count = len(react_components.get('details', []))
                        print(f"   âš›ï¸ React components: {'âœ… Found' if react_found else 'âŒ Not found'} ({react_count} detected)")
                    
                    if self.check_run_button_dom:
                        print(f"   ðŸ”˜ Run button in DOM: {'âœ… Found' if run_button_in_dom else 'âŒ Not found'}")
                    
                    print(f"   ðŸŽ¯ Overall game ready: {'âœ… YES' if overall_ready else 'âŒ NO'}")
                    
                    if overall_ready:
                        elapsed = time.time() - start_time
                        print(f"âœ… GAME VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if len(game_containers) < self.min_containers_required:
                            print(f"   â³ Waiting for game containers to load ({len(game_containers)}/{self.min_containers_required})")
                        if self.check_react_components and not react_components.get('found'):
                            print(f"   â³ Waiting for React components to initialize")
                        if self.check_run_button_dom and not run_button_in_dom:
                            print(f"   â³ Waiting for Run button to appear in DOM")
                
                else:
                    print(f"   âš ï¸ Game verification JavaScript failed")
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.game_verification_timeout:
                    print(f"âš ï¸ Game verification timeout ({self.game_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with image detection...")
                    return True
                
                # Wait before next attempt
                if attempt < max_attempts:
                    print(f"   â³ Waiting {self.game_verification_interval}s before next check...")
                    time.sleep(self.game_verification_interval)
            
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
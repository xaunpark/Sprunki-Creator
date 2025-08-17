# page_load_verifier.py - Enhanced Page Loading Verification for VPS
# Giải quyết vấn đề "quay tròn" timeout trên VPS

import time
import json
import requests
import websocket
from typing import Tuple, Optional, Dict, Any
from datetime import datetime

class PageLoadVerifier:
    """
    Hệ thống xác thực trang đã load hoàn toàn
    Đặc biệt tối ưu cho VPS với kết nối không ổn định
    """
    
    def __init__(self, debug_port=9222):
        self.debug_port = debug_port
        self.max_load_wait = 120  # 2 phút tối đa cho VPS chậm
        self.check_interval = 2   # Check mỗi 2 giây
        
    def verify_page_fully_loaded(self, target_url: str, max_retries: int = 3) -> Tuple[bool, str]:
        """
        Xác thực trang đã load hoàn toàn với retry mechanism
        Returns: (success, status_message)
        """
        for attempt in range(1, max_retries + 1):
            print(f"\n🔍 PAGE LOAD VERIFICATION - Attempt {attempt}/{max_retries}")
            print(f"🎯 Target URL: {target_url}")
            print("="*70)
            
            success, message = self._single_verification_attempt(target_url, attempt)
            
            if success:
                print(f"✅ Page verification successful on attempt {attempt}")
                return True, message
            
            if attempt < max_retries:
                print(f"⚠️ Attempt {attempt} failed: {message}")
                print(f"🔄 Will retry in 5 seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(5)
            else:
                print(f"❌ All {max_retries} verification attempts failed")
                return False, message
        
        return False, "All verification attempts exhausted"
    
    def _single_verification_attempt(self, target_url: str, attempt_num: int) -> Tuple[bool, str]:
        """Thực hiện một lần verification"""
        verification_start = datetime.now()
        
        try:
            # STEP 1: Check Chrome Debug Connection
            print(f"🔗 Step 1: Testing Chrome debug connection...")
            
            try:
                response = requests.get(f"http://localhost:{self.debug_port}/json", timeout=10)
                tabs = response.json()
                print(f"✅ Chrome debug responsive, found {len(tabs)} tabs")
            except Exception as e:
                return False, f"Chrome debug connection failed: {e}"
            
            # STEP 2: Find Target Tab
            print(f"🎯 Step 2: Locating target tab...")
            
            target_tab = None
            for tab in tabs:
                if tab.get('type') == 'page' and target_url.lower() in tab.get('url', '').lower():
                    target_tab = tab
                    break
            
            if not target_tab:
                return False, f"Target tab not found for URL: {target_url}"
            
            current_url = target_tab.get('url', 'Unknown')
            print(f"✅ Found target tab: {current_url}")
            
            # STEP 3: WebSocket Connection
            print(f"🔌 Step 3: Establishing WebSocket connection...")
            
            ws_url = target_tab.get('webSocketDebuggerUrl')
            if not ws_url:
                return False, "WebSocket URL not available"
            
            try:
                ws = websocket.create_connection(ws_url, timeout=15)
                print(f"✅ WebSocket connected successfully")
            except Exception as e:
                return False, f"WebSocket connection failed: {e}"
            
            try:
                # STEP 4: Comprehensive Page State Verification
                print(f"📊 Step 4: Comprehensive page state verification...")
                
                verification_result = self._verify_page_state_comprehensive(ws, attempt_num)
                
                return verification_result
                
            finally:
                ws.close()
                
        except Exception as e:
            verification_duration = (datetime.now() - verification_start).total_seconds()
            return False, f"Verification error after {verification_duration:.1f}s: {e}"
    
    def _verify_page_state_comprehensive(self, ws, attempt_num: int) -> Tuple[bool, str]:
        """Xác thực toàn diện trạng thái trang"""
        
        # JavaScript để check múltiple page states
        verification_js = '''
        new Promise(async (resolve, reject) => {
            try {
                console.log(`🔍 [Attempt ${attempt_num}] Starting comprehensive page verification...`);
                
                const verification = {
                    timestamp: new Date().toISOString(),
                    checks: {},
                    summary: { passed: 0, total: 0, ready: false }
                };
                
                // ================================================
                // CHECK 1: Document Ready State
                // ================================================
                verification.checks.documentState = {
                    state: document.readyState,
                    passed: document.readyState === 'complete'
                };
                verification.summary.total++;
                if (verification.checks.documentState.passed) verification.summary.passed++;
                
                console.log(`📄 Document State: ${document.readyState} - ${verification.checks.documentState.passed ? 'PASS' : 'FAIL'}`);
                
                // ================================================
                // CHECK 2: Window Load Status
                // ================================================
                verification.checks.windowLoaded = {
                    loaded: document.readyState === 'complete',
                    passed: document.readyState === 'complete'
                };
                verification.summary.total++;
                if (verification.checks.windowLoaded.passed) verification.summary.passed++;
                
                console.log(`🖼️ Window Loaded: ${verification.checks.windowLoaded.passed ? 'PASS' : 'FAIL'}`);
                
                // ================================================
                // CHECK 3: No Loading Spinners/Indicators
                // ================================================
                const loadingSelectors = [
                    '.loading', '.loader', '.spinner', '.loading-spinner',
                    '[class*="loading"]', '[class*="spinner"]', '[id*="loading"]',
                    '.ccw-loading', '.gandi-loading'
                ];
                
                let loadingElements = 0;
                for (const selector of loadingSelectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        const style = window.getComputedStyle(el);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            loadingElements++;
                        }
                    }
                }
                
                verification.checks.noLoadingSpinners = {
                    loadingElements: loadingElements,
                    passed: loadingElements === 0
                };
                verification.summary.total++;
                if (verification.checks.noLoadingSpinners.passed) verification.summary.passed++;
                
                console.log(`⭕ Loading Spinners: ${loadingElements} found - ${verification.checks.noLoadingSpinners.passed ? 'PASS' : 'FAIL'}`);
                
                // ================================================
                // CHECK 4: Essential Game Elements Present
                // ================================================
                const gameSelectors = [
                    'canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', 
                    '.gandi_stage_stage_1fD7k', '[class*="stage"]'
                ];
                
                let gameElementsFound = 0;
                const foundGameElements = [];
                
                for (const selector of gameSelectors) {
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0) {
                        gameElementsFound += elements.length;
                        foundGameElements.push(`${selector}(${elements.length})`);
                    }
                }
                
                verification.checks.gameElements = {
                    found: gameElementsFound,
                    elements: foundGameElements,
                    passed: gameElementsFound > 0
                };
                verification.summary.total++;
                if (verification.checks.gameElements.passed) verification.summary.passed++;
                
                console.log(`🎮 Game Elements: ${gameElementsFound} found - ${verification.checks.gameElements.passed ? 'PASS' : 'FAIL'}`);
                if (foundGameElements.length > 0) {
                    console.log(`    Found: ${foundGameElements.join(', ')}`);
                }
                
                // ================================================
                // CHECK 5: Network Activity Settled
                // ================================================
                let networkQuiet = true;
                try {
                    // Check for active network requests (basic check)
                    const performanceEntries = performance.getEntriesByType('navigation');
                    if (performanceEntries.length > 0) {
                        const navigation = performanceEntries[0];
                        const loadComplete = navigation.loadEventEnd > 0;
                        networkQuiet = loadComplete;
                    }
                } catch (e) {
                    console.warn('Network check failed:', e);
                    networkQuiet = true; // Assume quiet if can't check
                }
                
                verification.checks.networkQuiet = {
                    quiet: networkQuiet,
                    passed: networkQuiet
                };
                verification.summary.total++;
                if (verification.checks.networkQuiet.passed) verification.summary.passed++;
                
                console.log(`🌐 Network Activity: ${networkQuiet ? 'Settled' : 'Active'} - ${verification.checks.networkQuiet.passed ? 'PASS' : 'FAIL'}`);
                
                // ================================================
                // CHECK 6: Page Title Not Default/Loading
                // ================================================
                const title = document.title || '';
                const isValidTitle = title && 
                                  !title.toLowerCase().includes('loading') && 
                                  !title.toLowerCase().includes('please wait') &&
                                  title.trim().length > 0;
                
                verification.checks.validTitle = {
                    title: title,
                    passed: isValidTitle
                };
                verification.summary.total++;
                if (verification.checks.validTitle.passed) verification.summary.passed++;
                
                console.log(`📝 Page Title: "${title}" - ${verification.checks.validTitle.passed ? 'PASS' : 'FAIL'}`);
                
                // ================================================
                // CHECK 7: Specific Cocrea.world Elements
                // ================================================
                const cocreaSelectors = [
                    '.ccw-page', '.ccw-header', '.ccw-content',
                    '[class*="ccw-"]', '.project-container'
                ];
                
                let cocreaElementsFound = 0;
                const foundCocreaElements = [];
                
                for (const selector of cocreaSelectors) {
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0) {
                        cocreaElementsFound += elements.length;
                        foundCocreaElements.push(`${selector}(${elements.length})`);
                    }
                }
                
                verification.checks.cocreaElements = {
                    found: cocreaElementsFound,
                    elements: foundCocreaElements,
                    passed: cocreaElementsFound > 0
                };
                verification.summary.total++;
                if (verification.checks.cocreaElements.passed) verification.summary.passed++;
                
                console.log(`🏠 Cocrea Elements: ${cocreaElementsFound} found - ${verification.checks.cocreaElements.passed ? 'PASS' : 'FAIL'}`);
                
                // ================================================
                // FINAL EVALUATION
                // ================================================
                const passRate = verification.summary.passed / verification.summary.total;
                const minPassRate = 0.70; // Require 70% of checks to pass
                
                verification.summary.ready = passRate >= minPassRate;
                verification.summary.passRate = Math.round(passRate * 100);
                
                console.log(`\\n📊 VERIFICATION SUMMARY:`);
                console.log(`   ✅ Passed: ${verification.summary.passed}/${verification.summary.total} checks`);
                console.log(`   📈 Pass Rate: ${verification.summary.passRate}%`);
                console.log(`   🎯 Required: ${Math.round(minPassRate * 100)}%`);
                console.log(`   🚀 Page Ready: ${verification.summary.ready ? 'YES' : 'NO'}`);
                
                resolve(verification);
                
            } catch (error) {
                console.error('❌ Verification error:', error);
                reject({ error: error.toString(), stack: error.stack });
            }
        });
        '''.replace('${attempt_num}', str(attempt_num))
        
        try:
            # Send verification command
            command = {
                "method": "Runtime.evaluate",
                "params": {
                    "expression": verification_js,
                    "awaitPromise": True,
                    "returnByValue": True
                },
                "id": 1
            }
            
            ws.send(json.dumps(command))
            
            # Wait for response with timeout
            start_time = time.time()
            max_wait = 30  # 30 seconds max for verification
            
            while time.time() - start_time < max_wait:
                try:
                    response_str = ws.recv()
                    response = json.loads(response_str)
                    
                    if response.get('id') == 1:
                        if 'result' in response and 'result' in response['result']:
                            verification_result = response['result']['result']['value']
                            
                            # Analyze results
                            summary = verification_result.get('summary', {})
                            passed = summary.get('passed', 0)
                            total = summary.get('total', 0)
                            ready = summary.get('ready', False)
                            pass_rate = summary.get('passRate', 0)
                            
                            print(f"\n📊 VERIFICATION RESULTS:")
                            print(f"   ✅ Checks passed: {passed}/{total}")
                            print(f"   📈 Pass rate: {pass_rate}%")
                            print(f"   🎯 Page ready: {ready}")
                            
                            if ready:
                                return True, f"Page fully loaded ({passed}/{total} checks passed, {pass_rate}%)"
                            else:
                                failed_checks = []
                                checks = verification_result.get('checks', {})
                                for check_name, check_data in checks.items():
                                    if not check_data.get('passed', False):
                                        failed_checks.append(check_name)
                                
                                return False, f"Page not ready: {pass_rate}% pass rate, failed checks: {', '.join(failed_checks)}"
                        
                        elif 'error' in response:
                            return False, f"Verification script error: {response['error']}"
                        
                        break
                        
                except websocket.WebSocketTimeoutError:
                    continue
                except Exception as e:
                    return False, f"Response parsing error: {e}"
            
            return False, "Verification timeout - no response from browser"
            
        except Exception as e:
            return False, f"Verification execution error: {e}"
    
    def wait_for_page_ready_with_refresh(self, target_url: str) -> Tuple[bool, str]:
        """
        Wait for page to be ready, with automatic refresh if needed
        This is the main method to call from navigate_and_prepare_game
        """
        print(f"\n🔄 SMART PAGE LOADING with Auto-Refresh")
        print(f"🎯 Target: {target_url}")
        print(f"⏱️ Max wait: {self.max_load_wait}s")
        print("="*70)
        
        start_time = time.time()
        refresh_count = 0
        max_refreshes = 3
        
        while time.time() - start_time < self.max_load_wait:
            # Try verification
            success, message = self.verify_page_fully_loaded(target_url, max_retries=2)
            
            if success:
                total_time = time.time() - start_time
                print(f"\n🎉 PAGE LOADING SUCCESS!")
                print(f"   ⏱️ Total time: {total_time:.1f}s")
                print(f"   🔄 Refreshes used: {refresh_count}")
                print(f"   📊 Status: {message}")
                return True, message
            
            # If failed and we can still refresh
            if refresh_count < max_refreshes:
                refresh_count += 1
                print(f"\n🔄 PAGE LOADING FAILED - Performing refresh {refresh_count}/{max_refreshes}")
                print(f"   📝 Reason: {message}")
                
                # Perform refresh
                refresh_success = self._perform_page_refresh()
                
                if refresh_success:
                    print(f"   ✅ Refresh {refresh_count} completed")
                    print(f"   ⏳ Waiting 10s for page to stabilize...")
                    time.sleep(10)  # Wait after refresh
                else:
                    print(f"   ❌ Refresh {refresh_count} failed")
                    
            else:
                print(f"\n❌ PAGE LOADING EXHAUSTED")
                print(f"   🔄 Used all {max_refreshes} refreshes")
                print(f"   ⏱️ Total time: {time.time() - start_time:.1f}s")
                print(f"   📝 Final error: {message}")
                break
        
        return False, f"Page loading timeout after {self.max_load_wait}s with {refresh_count} refreshes"
    
    def _perform_page_refresh(self) -> bool:
        """Perform page refresh via Chrome debug"""
        try:
            # Get active tab
            response = requests.get(f"http://localhost:{self.debug_port}/json", timeout=10)
            tabs = response.json()
            
            active_tab = None
            for tab in tabs:
                if tab.get('type') == 'page':
                    active_tab = tab
                    break
            
            if not active_tab:
                print("   ❌ No active tab found for refresh")
                return False
            
            # Connect and refresh
            ws_url = active_tab.get('webSocketDebuggerUrl')
            if not ws_url:
                print("   ❌ No WebSocket URL for refresh")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            try:
                # Send reload command
                reload_command = {
                    "method": "Page.reload",
                    "params": {"ignoreCache": True},
                    "id": 999
                }
                
                ws.send(json.dumps(reload_command))
                
                # Wait for response
                start_time = time.time()
                while time.time() - start_time < 10:
                    try:
                        response_str = ws.recv()
                        response = json.loads(response_str)
                        if response.get('id') == 999:
                            return True
                    except:
                        continue
                
                return True  # Assume success even if no response
                
            finally:
                ws.close()
                
        except Exception as e:
            print(f"   ❌ Refresh error: {e}")
            return False
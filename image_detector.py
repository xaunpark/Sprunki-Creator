# --- START OF FILE image_detector.py (SERVICE MODE OPTIMIZED) ---

import pyautogui
import cv2
import time
from pathlib import Path
import pygetwindow as gw

class ImageDetector:
    """
    Lớp nhận dạng và tương tác với các yếu tố UI bằng hình ảnh.
    Được tối ưu hóa cho quy trình của cocrea.world.
    Đóng gói logic phức tạp như cuộn trang và chờ đợi.
    🔧 SERVICE MODE OPTIMIZED: Enhanced for reliable operation without GUI
    """
    
    def __init__(self, domain="cocrea.world", config_manager=None):
        self.domain = domain
        
        # Load configuration
        if config_manager is None:
            from config_manager import get_config
            config_manager = get_config()
        
        self.config = config_manager
        
        # Detection settings from config
        detection_config = self.config.get('image_detection', {})
        self.confidence = detection_config.get('confidence_threshold', 0.85)
        self.default_timeout = self.config.get('game.default_timeout', 30)
        self.retry_interval = detection_config.get('retry_interval', 0.5)
        
        # 🔧 SERVICE MODE: Extended timeouts and retries
        self.service_mode_timeout = 45  # Longer timeout for service mode
        self.service_mode_retry_interval = 1.0  # Longer retry interval
        self.max_activation_attempts = 5  # Multiple activation attempts
        
        # Assets path from config
        assets_base = detection_config.get('assets_directory', 'image_assets')
        self.assets_path = Path(assets_base) / domain
        
        # PyAutoGUI settings - optimized for service mode
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.2  # Longer pause for service mode
        
        print(f"🔍 ImageDetector configured from config.json:")
        print(f"   Confidence: {self.confidence}")
        print(f"   Timeout: {self.default_timeout}s")
        print(f"   Service Mode Timeout: {self.service_mode_timeout}s")
        print(f"   Assets: {self.assets_path}")

    # ================================================================
    # == PRIVATE HELPER METHODS                                   ==
    # ================================================================

    def _load_image_path(self, image_name):
        """Tải và xác thực đường dẫn của một ảnh asset."""
        image_path = self.assets_path / image_name
        if not image_path.exists():
            print(f"❌ Image asset not found: {image_path}")
            return None
        return str(image_path)

    def _find_image(self, image_name, region=None):
        """
        Phương thức private cốt lõi để tìm một ảnh trên màn hình.
        Trả về tọa độ (x, y) nếu tìm thấy, ngược lại trả về None.
        🔧 SERVICE MODE: Enhanced with window activation
        """
        # 🔧 SERVICE MODE FIX: Ensure window focus before image search
        self._ensure_window_focus_enhanced()
        
        image_path = self._load_image_path(image_name)
        if not image_path:
            return None
            
        try:
            return pyautogui.locateCenterOnScreen(
                image_path, 
                confidence=self.confidence,
                region=region
            )
        except pyautogui.ImageNotFoundException:
            return None
        except Exception as e:
            # Ngăn chặn các lỗi khác của pyautogui làm dừng chương trình
            # print(f"⚠️ An unexpected error occurred during image search: {e}")
            return None

    def _focus_browser_window(self):
        """Click vào giữa màn hình để đảm bảo cửa sổ trình duyệt được focus."""
        try:
            screen_width, screen_height = pyautogui.size()
            center_x, center_y = screen_width // 2, screen_height // 2
            
            print(f"🎯 Focusing browser window at ({center_x}, {center_y})")
            pyautogui.click(center_x, center_y)
            time.sleep(0.5)  # Longer wait for service mode
            return True
        except Exception as e:
            print(f"⚠️ Could not focus browser: {e}")
            return False

    # ================================================================
    # == SERVICE MODE ENHANCED METHODS                            ==
    # ================================================================

    def _ensure_window_focus_enhanced(self):
        """🔧 SERVICE MODE: Enhanced window focus with multiple attempts"""
        try:
            # Find Chrome window with cocrea.world
            chrome_windows = gw.getWindowsWithTitle('cocrea.world')
            if not chrome_windows:
                chrome_windows = gw.getWindowsWithTitle('Chrome')
            
            if chrome_windows:
                target_window = chrome_windows[0]
                
                # 🔧 SERVICE MODE: Multiple activation attempts
                for attempt in range(self.max_activation_attempts):
                    try:
                        # Comprehensive activation sequence
                        target_window.activate()
                        target_window.restore()  # Ensure not minimized
                        target_window.maximize()  # Ensure maximized
                        time.sleep(0.8)  # Longer wait for activation
                        
                        # Move mouse to center and click to ensure focus
                        pyautogui.moveTo(960, 540, duration=0.3)
                        time.sleep(0.2)
                        pyautogui.click(960, 540)
                        time.sleep(0.3)
                        
                        print(f"✅ SERVICE MODE: Window activated (attempt {attempt + 1})")
                        return True
                        
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "error code from windows: 0" in error_msg:
                            print(f"✅ SERVICE MODE: Window activated with Windows quirk (attempt {attempt + 1})")
                            return True
                        else:
                            print(f"⚠️ SERVICE MODE: Activation attempt {attempt + 1} failed: {e}")
                            if attempt < self.max_activation_attempts - 1:
                                time.sleep(1.0)  # Wait before retry
                
                print("⚠️ SERVICE MODE: All activation attempts completed")
                return True  # Proceed anyway
            else:
                print("⚠️ SERVICE MODE: No Chrome window found for focus")
                return False
                
        except Exception as e:
            print(f"⚠️ SERVICE MODE: Enhanced window focus error: {e}")
            return False

    def _perform_enhanced_click(self, coords):
        """🔧 SERVICE MODE: Enhanced click with comprehensive activation"""
        try:
            x, y = coords
            
            # Pre-click activation
            print(f"🔧 SERVICE MODE: Pre-click window activation...")
            self._ensure_window_focus_enhanced()
            
            # Enhanced click sequence
            print(f"   🖱️ Enhanced click at ({x}, {y})")
            
            # Move to position
            pyautogui.moveTo(x, y, duration=0.3)
            time.sleep(0.3)
            
            # Click sequence with retries
            for attempt in range(3):
                try:
                    pyautogui.click(x, y)
                    time.sleep(0.5)
                    print(f"   ✅ Click attempt {attempt + 1} completed")
                    
                    if attempt == 0:
                        # Additional clicks for reliability
                        time.sleep(0.2)
                        pyautogui.click(x, y)
                        time.sleep(0.3)
                    
                except Exception as e:
                    print(f"   ⚠️ Click attempt {attempt + 1} failed: {e}")
                    if attempt < 2:
                        time.sleep(0.5)
            
            print("✅ SERVICE MODE: Enhanced click sequence completed")
            return True
            
        except Exception as e:
            print(f"❌ SERVICE MODE: Enhanced click failed: {e}")
            return False

    def _service_mode_page_operation(self, operation_name, operation_func):
        """🔧 SERVICE MODE: Wrapper for page operations with activation"""
        try:
            print(f"🔧 SERVICE MODE: Executing {operation_name}...")
            
            # Ensure window focus before operation
            self._ensure_window_focus_enhanced()
            time.sleep(0.5)
            
            # Execute operation
            result = operation_func()
            
            # Wait after operation
            time.sleep(1.0)
            
            return result
            
        except Exception as e:
            print(f"❌ SERVICE MODE: {operation_name} failed: {e}")
            return False

    # ================================================================
    # == PUBLIC API METHODS (Used by GamePlayer)                  ==
    # ================================================================

    def click_run_button(self, max_pagedown_attempts=5):
        """
        🔧 SERVICE MODE OPTIMIZED: Tìm và click vào nút "Run" với enhanced service mode support
        Returns: True nếu click thành công, False nếu thất bại.
        """
        print("\n▶️ SERVICE MODE: Finding and clicking 'Run' button...")
        
        # 🔧 SERVICE MODE: Enhanced initial activation
        print("🔧 SERVICE MODE: Initial comprehensive window activation...")
        for i in range(3):
            self._ensure_window_focus_enhanced()
            time.sleep(1.0)
        
        # 1. Thử tìm ngay trên màn hình hiện tại
        print("🔧 SERVICE MODE: Searching on current screen...")
        coords = self._find_image("run_button.png")
        if coords:
            print("✅ SERVICE MODE: Found 'Run' on current screen. Clicking...")
            return self._perform_enhanced_click(coords)

        # 2. Đi lên đầu trang và thử lại
        print("🔧 SERVICE MODE: Searching from top of page...")
        def go_to_top():
            pyautogui.press('home')
            return True
        
        self._service_mode_page_operation("go_to_top", go_to_top)
        time.sleep(2.0)  # Longer wait for service mode
        
        coords = self._find_image("run_button.png")
        if coords:
            print("✅ SERVICE MODE: Found 'Run' at top of page. Clicking...")
            return self._perform_enhanced_click(coords)

        # 3. Sử dụng Page Down để tìm kiếm với service mode enhancements
        print(f"🔧 SERVICE MODE: Page Down search (max {max_pagedown_attempts} attempts)...")
        for attempt in range(max_pagedown_attempts):
            print(f"   ... SERVICE MODE Page Down attempt {attempt + 1}/{max_pagedown_attempts}")
            
            # Enhanced page down with activation
            def page_down_operation():
                pyautogui.press('pagedown')
                return True
            
            self._service_mode_page_operation(f"pagedown_{attempt+1}", page_down_operation)
            time.sleep(1.5)  # Longer wait for page to stabilize
            
            coords = self._find_image("run_button.png")
            if coords:
                print(f"✅ SERVICE MODE: Found 'Run' after Page Down attempt {attempt + 1}. Clicking...")
                return self._perform_enhanced_click(coords)
        
        print("❌ SERVICE MODE: 'Run' button not found after all attempts.")
        return False

    def click_fullscreen_button(self, wait_time=8):
        """
        🔧 SERVICE MODE OPTIMIZED: Chờ, tìm và click vào nút "Fullscreen"
        Returns: True nếu click thành công, False nếu không tìm thấy trong thời gian chờ.
        """
        print("\n🖥️ SERVICE MODE: Finding and clicking 'Fullscreen' button...")
        
        # 🔧 SERVICE MODE: Enhanced initial activation
        print("🔧 SERVICE MODE: Initial activation for fullscreen detection...")
        self._ensure_window_focus_enhanced()
        
        start_time = time.time()
        extended_wait_time = wait_time + 5  # Extra time for service mode
        
        print(f"🔧 SERVICE MODE: Waiting up to {extended_wait_time}s for fullscreen button...")
        while time.time() - start_time < extended_wait_time:
            # Enhanced search with activation
            coords = self._find_image("fullscreen_button.png")
            if coords:
                print("✅ SERVICE MODE: Found 'Fullscreen' button. Clicking...")
                return self._perform_enhanced_click(coords)
            
            # Longer retry interval for service mode
            time.sleep(self.service_mode_retry_interval)

        print("❌ SERVICE MODE: 'Fullscreen' button not found within wait time.")
        return False

    def find_and_click(self, image_name, description="element", timeout=5):
        """
        🔧 SERVICE MODE OPTIMIZED: Phương thức chung để tìm và click một hình ảnh bất kỳ
        Returns: True nếu click thành công, False nếu thất bại.
        """
        print(f"🖱️ SERVICE MODE: Generic find and click for '{description}' ({image_name})...")
        
        # Extended timeout for service mode
        extended_timeout = timeout + 5
        start_time = time.time()
        
        # Initial activation
        self._ensure_window_focus_enhanced()
        
        while time.time() - start_time < extended_timeout:
            coords = self._find_image(image_name)
            if coords:
                print(f"✅ SERVICE MODE: Found '{description}', clicking...")
                return self._perform_enhanced_click(coords)
            
            # Re-activate periodically during search
            if (time.time() - start_time) % 3 < self.service_mode_retry_interval:
                self._ensure_window_focus_enhanced()
            
            time.sleep(self.service_mode_retry_interval)
            
        print(f"⏰ SERVICE MODE: Timeout reached. Could not find '{description}'.")
        return False

    # ================================================================
    # == LEGACY METHODS (for backward compatibility)              ==
    # ================================================================

    def _ensure_window_focus(self):
        """Legacy method - redirects to enhanced version"""
        return self._ensure_window_focus_enhanced()

# --- END OF FILE image_detector.py (SERVICE MODE OPTIMIZED) ---
# --- START OF FILE image_detector.py (REFINED) ---

import pyautogui
import cv2
import time
from pathlib import Path

class ImageDetector:
    """
    Lớp nhận dạng và tương tác với các yếu tố UI bằng hình ảnh.
    Được tối ưu hóa cho quy trình của cocrea.world.
    Đóng gói logic phức tạp như cuộn trang và chờ đợi.
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
        
        # Assets path from config
        assets_base = detection_config.get('assets_directory', 'image_assets')
        self.assets_path = Path(assets_base) / domain
        
        # PyAutoGUI settings
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1
        
        print(f"🔍 ImageDetector configured from config.json:")
        print(f"   Confidence: {self.confidence}")
        print(f"   Timeout: {self.default_timeout}s")
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
        """
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
            time.sleep(0.3)
            return True
        except Exception as e:
            print(f"⚠️ Could not focus browser: {e}")
            return False

    # ================================================================
    # == PUBLIC API METHODS (Used by GamePlayer)                  ==
    # ================================================================

    def click_run_button(self, max_pagedown_attempts=5):
        """
        Tìm và click vào nút "Run" sử dụng chiến lược cuộn Page Down.
        Đây là phương thức chuyên dụng và hiệu quả nhất cho nút Run.
        Returns: True nếu click thành công, False nếu thất bại.
        """
        print("\n▶️ Finding and clicking 'Run' button...")
        self._focus_browser_window()
        
        # 1. Thử tìm ngay trên màn hình hiện tại
        coords = self._find_image("run_button.png")
        if coords:
            print("✅ Found 'Run' on the current screen. Clicking...")
            pyautogui.click(coords)
            return True

        # 2. Đi lên đầu trang và thử lại
        print("   Searching from the top of the page...")
        pyautogui.press('home')
        time.sleep(1.5)
        coords = self._find_image("run_button.png")
        if coords:
            print("✅ Found 'Run' at the top of the page. Clicking...")
            pyautogui.click(coords)
            return True

        # 3. Sử dụng Page Down để tìm kiếm
        print(f"   Searching using Page Down (max {max_pagedown_attempts} attempts)...")
        for attempt in range(max_pagedown_attempts):
            print(f"   ... Page Down attempt {attempt + 1}/{max_pagedown_attempts}")
            pyautogui.press('pagedown')
            time.sleep(1.0)  # Chờ trang ổn định
            
            coords = self._find_image("run_button.png")
            if coords:
                print(f"✅ Found 'Run' after Page Down. Clicking...")
                pyautogui.click(coords)
                return True
        
        print("❌ 'Run' button not found after all attempts.")
        return False

    def click_fullscreen_button(self, wait_time=8):
        """
        Chờ, tìm và click vào nút "Fullscreen".
        Nút này chỉ xuất hiện sau khi game đã tải, nên cần có logic chờ đợi.
        Tìm kiếm chỉ diễn ra trên màn hình hiện tại (không cuộn).
        Returns: True nếu click thành công, False nếu không tìm thấy trong thời gian chờ.
        """
        print("\n🖥️ Finding and clicking 'Fullscreen' button...")
        start_time = time.time()
        
        print(f"   Waiting up to {wait_time}s for the button to appear...")
        while time.time() - start_time < wait_time:
            coords = self._find_image("fullscreen_button.png")
            if coords:
                print("✅ Found 'Fullscreen' button. Clicking...")
                pyautogui.click(coords)
                return True
            
            time.sleep(self.retry_interval)

        print("❌ 'Fullscreen' button not found within the wait time.")
        return False

    def find_and_click(self, image_name, description="element", timeout=5):
        """
        Phương thức chung để tìm và click một hình ảnh bất kỳ.
        Hữu ích cho các nút khác trong tương lai.
        Tìm kiếm chỉ diễn ra trên màn hình hiện tại.
        Returns: True nếu click thành công, False nếu thất bại.
        """
        print(f"🖱️ Generic find and click for '{description}' ({image_name})...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            coords = self._find_image(image_name)
            if coords:
                try:
                    pyautogui.click(coords)
                    print(f"✅ Successfully clicked '{description}'")
                    return True
                except Exception as e:
                    print(f"❌ Click failed for '{description}': {e}")
                    return False
            
            time.sleep(self.retry_interval)
            
        print(f"⏰ Timeout reached. Could not find and click '{description}'.")
        return False

# --- END OF FILE image_detector.py (REFINED) ---
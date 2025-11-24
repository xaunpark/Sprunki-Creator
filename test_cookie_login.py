import os
import json
import time
import subprocess
import requests
import websocket
import sys
import shutil

# --- CẤU HÌNH ---
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe") # Thêm đường dẫn local
]
DEBUG_PORT = 9222
COOKIE_FILE = "cocrea_cookies.json"
TARGET_URL = "https://cocrea.world"
TEMP_PROFILE_DIR = os.path.join(os.getcwd(), "chrome_temp_profile") # Thư mục profile tạm

def get_chrome_path():
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None

def cleanup_temp_profile():
    """Xóa thư mục profile tạm để dọn dẹp"""
    if os.path.exists(TEMP_PROFILE_DIR):
        try:
            shutil.rmtree(TEMP_PROFILE_DIR, ignore_errors=True)
            print("🧹 Đã dọn dẹp thư mục profile tạm.")
        except:
            pass

def inject_cookies(ws):
    if not os.path.exists(COOKIE_FILE):
        print(f"❌ Không tìm thấy file '{COOKIE_FILE}'")
        return False

    try:
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        
        print(f"🍪 Đang nạp {len(cookies)} cookies...")
        success_count = 0
        
        for i, cookie in enumerate(cookies):
            # Xử lý domain: Cocrea yêu cầu domain bắt đầu bằng dấu chấm
            domain = cookie.get('domain', '')
            if domain == "www.cocrea.world":
                domain = ".cocrea.world" # Fix cứng domain chung
            
            cookie_params = {
                "name": cookie.get('name'),
                "value": cookie.get('value'),
                "domain": domain,
                "path": cookie.get('path', '/'),
                "secure": cookie.get('secure', True),
                "httpOnly": cookie.get('httpOnly', False),
                "sameSite": "Lax" # Force Lax để dễ chấp nhận
            }
            
            if 'expirationDate' in cookie:
                cookie_params['expires'] = cookie['expirationDate']

            ws.send(json.dumps({
                "id": i + 1,
                "method": "Network.setCookie",
                "params": cookie_params
            }))
            
            # Nhận phản hồi nhanh (không chặn)
            result = ws.recv()
            if '"success":true' in result:
                success_count += 1

        print(f"✅ Đã gửi lệnh tiêm {success_count}/{len(cookies)} cookies.")
        return True
    except Exception as e:
        print(f"❌ Lỗi injection: {e}")
        return False

def main():
    print("="*50)
    print("🧪 TEST COOKIE INJECTION V2 (FIXED CONNECTION)")
    print("="*50)

    # 1. Dọn dẹp môi trường cũ
    os.system("taskkill /f /im chrome.exe >nul 2>&1")
    cleanup_temp_profile()
    time.sleep(1)

    chrome_exe = get_chrome_path()
    if not chrome_exe:
        print("❌ Không tìm thấy Chrome.")
        return

    # 2. Mở Chrome với User Data Dir riêng biệt (QUAN TRỌNG NHẤT)
    print(f"🚀 Mở Chrome với Profile tạm tại: {TEMP_PROFILE_DIR}")
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={TEMP_PROFILE_DIR}",  # <--- FIX LỖI KẾT NỐI Ở ĐÂY
        "--incognito",
        "--no-first-run",
        "--disable-gpu",
        "--window-size=1280,800",
        "--remote-allow-origins=*", # Cho phép websocket kết nối thoải mái
        TARGET_URL
    ]
    
    # Chạy Chrome
    proc = subprocess.Popen(cmd)
    
    print("⏳ Đang chờ Chrome khởi động (5s)...")
    time.sleep(5)

    # 3. Kết nối WebSocket
    ws_url = None
    try:
        response = requests.get(f"http://localhost:{DEBUG_PORT}/json", timeout=5)
        tabs = response.json()
        # Tìm tab Cocrea
        for tab in tabs:
            if 'cocrea' in tab.get('url', ''):
                ws_url = tab['webSocketDebuggerUrl']
                break
        # Fallback nếu chưa load xong URL
        if not ws_url and tabs:
            ws_url = tabs[0]['webSocketDebuggerUrl']
    except Exception as e:
        print(f"❌ Vẫn không kết nối được: {e}")
        print("👉 Hãy thử chạy lệnh mở Chrome bằng tay trong CMD để xem lỗi gì.")
        proc.kill()
        return

    if not ws_url:
        print("❌ Chrome mở nhưng không lấy được WebSocket URL.")
        proc.kill()
        return

    print("🔗 Kết nối thành công!")
    ws = websocket.create_connection(ws_url)

    # 4. Tiêm và Reload
    if inject_cookies(ws):
        print("🔄 Đang reload trang...")
        ws.send(json.dumps({"id": 9999, "method": "Page.reload"}))
        
        print("\n" + "="*50)
        print("✅ KIỂM TRA KẾT QUẢ TRÊN TRÌNH DUYỆT")
        print("1. Nếu thấy Avatar/Logged in -> Thành công.")
        print("2. Nếu vẫn chưa login -> Cookie hết hạn hoặc sai domain.")
        print("="*50)

    # Giữ cửa sổ để xem
    input("\nNhấn Enter để đóng test...")
    ws.close()
    proc.kill()
    cleanup_temp_profile()

if __name__ == "__main__":
    main()
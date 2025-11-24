import os
import json
import time
import subprocess
import requests
import websocket
import sys

# --- CẤU HÌNH ---
# Đường dẫn Chrome trên máy bạn (Hãy sửa lại nếu khác)
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
]
DEBUG_PORT = 9222
COOKIE_FILE = "cocrea_cookies.json"
TARGET_URL = "https://cocrea.world"

def get_chrome_path():
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None

def get_websocket_url():
    """Lấy URL WebSocket để điều khiển Chrome"""
    try:
        response = requests.get(f"http://localhost:{DEBUG_PORT}/json", timeout=5)
        tabs = response.json()
        for tab in tabs:
            if tab['type'] == 'page' and 'cocrea' in tab['url']:
                return tab['webSocketDebuggerUrl']
        # Nếu không tìm thấy tab cocrea, lấy tab đầu tiên
        if tabs:
            return tabs[0]['webSocketDebuggerUrl']
    except Exception as e:
        print(f"❌ Không thể kết nối tới Chrome Debug: {e}")
    return None

def inject_cookies(ws):
    """Đọc file và gửi lệnh setCookie qua WebSocket"""
    if not os.path.exists(COOKIE_FILE):
        print(f"❌ LỖI: Không tìm thấy file '{COOKIE_FILE}'")
        print("👉 Hãy Export cookie từ trình duyệt thật và lưu vào file này trước.")
        return False

    try:
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        
        print(f"🍪 Đang nạp {len(cookies)} cookies vào Incognito...")
        
        success_count = 0
        id_counter = 1

        for cookie in cookies:
            # Chuẩn hóa domain (quan trọng với Cocrea)
            domain = cookie.get('domain', '')
            if not domain.startswith('.'):
                domain = '.' + domain
                
            # Lọc các trường dữ liệu cần thiết cho CDP
            cookie_params = {
                "name": cookie.get('name'),
                "value": cookie.get('value'),
                "domain": domain, # Ép domain chính xác
                "path": cookie.get('path', '/'),
                "secure": cookie.get('secure', True),
                "httpOnly": cookie.get('httpOnly', False),
                "sameSite": cookie.get('sameSite', 'Lax')
            }
            
            # Xử lý thời hạn cookie
            if 'expirationDate' in cookie:
                cookie_params['expires'] = cookie['expirationDate']

            # Gửi lệnh setCookie
            command = {
                "id": id_counter,
                "method": "Network.setCookie",
                "params": cookie_params
            }
            ws.send(json.dumps(command))
            result = ws.recv()
            
            # Kiểm tra phản hồi (đơn giản)
            if '"success":true' in result:
                success_count += 1
            
            id_counter += 1

        print(f"✅ Đã gửi lệnh tiêm {success_count} cookies.")
        return True

    except Exception as e:
        print(f"❌ Lỗi khi xử lý file JSON cookie: {e}")
        return False

def main():
    print("="*50)
    print("🧪 TEST COOKIE INJECTION (INCOGNITO)")
    print("="*50)

    # 1. Tìm Chrome
    chrome_exe = get_chrome_path()
    if not chrome_exe:
        print("❌ Không tìm thấy Chrome. Hãy kiểm tra lại đường dẫn.")
        return

    # 2. Kill Chrome cũ để tránh xung đột port
    print("🧹 Đang đóng các Chrome cũ...")
    os.system("taskkill /f /im chrome.exe >nul 2>&1")
    time.sleep(1)

    # 3. Mở Chrome Incognito với Debug Port
    print("🚀 Đang mở Chrome Ẩn danh...")
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--incognito",
        "--no-first-run",
        "--window-size=1280,800",
        TARGET_URL
    ]
    subprocess.Popen(cmd)
    
    # Chờ Chrome khởi động
    print("⏳ Chờ trình duyệt khởi động (5s)...")
    time.sleep(5)

    # 4. Kết nối WebSocket
    ws_url = get_websocket_url()
    if not ws_url:
        print("❌ Không lấy được WebSocket URL. Test thất bại.")
        return

    ws = websocket.create_connection(ws_url)
    print("🔗 Đã kết nối tới Chrome qua CDP.")

    # 5. Tiêm Cookies
    if inject_cookies(ws):
        print("🔄 Đang reload trang để áp dụng cookies...")
        ws.send(json.dumps({"id": 999, "method": "Page.reload"}))
        time.sleep(3)
        
        print("\n" + "="*50)
        print("✅ TEST HOÀN TẤT!")
        print("👉 Hãy nhìn vào trình duyệt đang mở.")
        print("👉 Nếu bạn thấy Avatar/Tên đăng nhập ở góc trên bên phải => THÀNH CÔNG.")
        print("👉 Nếu vẫn thấy nút 'Login' => THẤT BẠI (Cookie sai hoặc hết hạn).")
        print("="*50)
    
    ws.close()
    # Không đóng trình duyệt để bạn kịp nhìn kết quả
    input("\nNhấn Enter để đóng trình duyệt và kết thúc test...")
    os.system("taskkill /f /im chrome.exe >nul 2>&1")

if __name__ == "__main__":
    # Cài đặt thư viện nếu thiếu
    try:
        import websocket
        import requests
    except ImportError:
        print("⚠️ Thiếu thư viện. Đang cài đặt...")
        os.system(f"{sys.executable} -m pip install websocket-client requests")
        import websocket
        import requests
        
    main()
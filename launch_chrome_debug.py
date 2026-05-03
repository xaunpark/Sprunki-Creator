"""
launch_chrome_debug.py
----------------------
Mở Chrome với profile cá nhân + remote-debugging-port=9222,
để script detect_game_panels.py (và toàn bộ hệ thống Sprunki) có thể kết nối.

Cách dùng:
    python launch_chrome_debug.py                  # tự động detect profile đang chạy game
    python launch_chrome_debug.py --profile Default
    python launch_chrome_debug.py --profile "Profile 1"
    python launch_chrome_debug.py --list           # liệt kê tất cả profiles
    python launch_chrome_debug.py --url https://cocrea.world/@user/mod  # mở URL cụ thể
"""

import subprocess
import sys
import os
import time
import json
import argparse
import psutil
import requests
from pathlib import Path

# =============================================================================
# CẤU HÌNH
# =============================================================================
CHROME_USER_DATA = r"C:\Users\dinht\AppData\Local\Google\Chrome\User Data"
DEBUG_PORT = 9222

CHROME_EXECUTABLES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\dinht\AppData\Local\Google\Chrome\Application\chrome.exe",
]

# Chrome flags cần thiết
CHROME_FLAGS = [
    f"--remote-debugging-port={DEBUG_PORT}",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-client-side-phishing-detection",
    "--disable-sync-preferences",
    "--remote-allow-origins=*",
    # KHÔNG thêm --incognito — vì cần dùng profile cá nhân
]


# =============================================================================
# Tìm Chrome executable
# =============================================================================
def find_chrome():
    for path in CHROME_EXECUTABLES:
        if os.path.exists(path):
            return path
    # Thử tìm trong PATH
    try:
        result = subprocess.run(["where", "chrome"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
    except Exception:
        pass
    return None


# =============================================================================
# Đọc tên hiển thị của từng profile
# =============================================================================
def get_profiles(user_data_dir):
    profiles = []
    base = Path(user_data_dir)
    for folder in sorted(base.iterdir()):
        if not folder.is_dir():
            continue
        name = folder.name
        if not (name == "Default" or name.startswith("Profile")):
            continue
        pref_file = folder / "Preferences"
        display_name = name
        if pref_file.exists():
            try:
                with open(pref_file, "r", encoding="utf-8", errors="replace") as f:
                    pref = json.load(f)
                display_name = pref.get("profile", {}).get("name", name)
            except Exception:
                pass
        profiles.append({"dir": name, "display": display_name})
    return profiles


# =============================================================================
# Kiểm tra xem Chrome có đang chạy với debug port không
# =============================================================================
def is_debug_chrome_running(port=DEBUG_PORT):
    try:
        resp = requests.get(f"http://localhost:{port}/json", timeout=2)
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    return False


# =============================================================================
# Tìm profile đang được dùng bởi Chrome process hiện tại
# =============================================================================
def detect_current_chrome_profile():
    """
    Đọc command line của Chrome process đang chạy để tìm --profile-directory.
    """
    found = []
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            name = proc.info.get('name', '').lower()
            if 'chrome' not in name:
                continue
            cmdline = proc.info.get('cmdline') or []
            for arg in cmdline:
                if arg.startswith('--profile-directory='):
                    profile_dir = arg.split('=', 1)[1].strip().strip('"')
                    if profile_dir not in found:
                        found.append(profile_dir)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


# =============================================================================
# Kill Chrome (cần thiết để relaunch với debug port)
# =============================================================================
def kill_chrome():
    killed = 0
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            if 'chrome' in (proc.info.get('name') or '').lower():
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        print(f"  [OK] Đã đóng {killed} Chrome process(es)")
        time.sleep(2)  # Chờ OS giải phóng port và file lock
    else:
        print("  [OK] Chrome chưa mở")


# =============================================================================
# Launch Chrome với profile và debug port
# =============================================================================
def launch_chrome(profile_dir, chrome_exe, url=None, extra_flags=None):
    cmd = [chrome_exe]
    cmd += CHROME_FLAGS
    cmd.append(f"--user-data-dir={CHROME_USER_DATA}")
    cmd.append(f"--profile-directory={profile_dir}")
    if extra_flags:
        cmd += extra_flags
    if url:
        cmd.append(url)

    print(f"\n[CMD] {' '.join(cmd[:5])} ...")
    print(f"      --user-data-dir={CHROME_USER_DATA}")
    print(f"      --profile-directory={profile_dir}")
    if url:
        print(f"      URL: {url}")

    subprocess.Popen(cmd)


# =============================================================================
# Kiểm tra debug port đã sẵn sàng chưa
# =============================================================================
def wait_for_debug_port(port=DEBUG_PORT, timeout=15):
    print(f"\n[...] Chờ Chrome debug port {port} sẵn sàng", end="", flush=True)
    for _ in range(timeout * 2):
        if is_debug_chrome_running(port):
            print(" ✅")
            return True
        print(".", end="", flush=True)
        time.sleep(0.5)
    print(" ❌ TIMEOUT")
    return False


# =============================================================================
# In danh sách tabs đang mở
# =============================================================================
def print_open_tabs(port=DEBUG_PORT):
    try:
        resp = requests.get(f"http://localhost:{port}/json", timeout=5)
        tabs = resp.json()
        print(f"\n[INFO] Các tab đang mở ({len(tabs)} tabs):")
        for i, tab in enumerate(tabs):
            tab_type = tab.get('type', '?')
            title = tab.get('title', '')[:60]
            url = tab.get('url', '')[:80]
            print(f"  [{i}] [{tab_type}] {title!r}")
            print(f"       {url}")
    except Exception as e:
        print(f"[WARN] Không đọc được tabs: {e}")


# =============================================================================
# Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Mở Chrome debug với profile cá nhân")
    parser.add_argument("--profile", default=None,
                        help="Tên thư mục profile (ví dụ: Default, 'Profile 1')")
    parser.add_argument("--list", action="store_true",
                        help="Liệt kê tất cả profiles")
    parser.add_argument("--url", default=None,
                        help="URL mở khi khởi động (mặc định: không mở URL mới)")
    parser.add_argument("--no-kill", action="store_true",
                        help="Không kill Chrome trước khi mở (nếu debug port chưa chạy)")
    parser.add_argument("--scan", action="store_true",
                        help="Sau khi mở Chrome, tự động chạy detect_game_panels.py")
    args = parser.parse_args()

    print("=" * 60)
    print("  CHROME DEBUG LAUNCHER - Sprunki Project")
    print("=" * 60)

    # --- Tìm Chrome ---
    chrome_exe = find_chrome()
    if not chrome_exe:
        print("[FATAL] Không tìm thấy Chrome executable!")
        print("  Kiểm tra lại CHROME_EXECUTABLES trong script.")
        sys.exit(1)
    print(f"[OK] Chrome: {chrome_exe}")

    # --- Liệt kê profiles ---
    profiles = get_profiles(CHROME_USER_DATA)

    if args.list:
        print(f"\n[INFO] Profiles tại: {CHROME_USER_DATA}")
        print(f"  {'Folder':<15} {'Tên hiển thị'}")
        print("  " + "-" * 40)
        for p in profiles:
            print(f"  {p['dir']:<15} {p['display']}")
        print("\n  Dùng: python launch_chrome_debug.py --profile \"Profile X\"")
        return

    # --- Chọn profile ---
    profile_dir = args.profile

    if not profile_dir:
        # Thử detect từ Chrome đang chạy
        detected = detect_current_chrome_profile()
        if detected:
            profile_dir = detected[0]
            print(f"\n[AUTO-DETECT] Chrome đang dùng profile: {profile_dir!r}")
            # Hiển thị tên đẹp
            for p in profiles:
                if p['dir'] == profile_dir:
                    print(f"              Tên: {p['display']!r}")
                    break
        else:
            # Fallback: hỏi user
            print(f"\n[PROFILES] Không detect được Chrome đang chạy.")
            print(f"  {'#':<3} {'Folder':<15} {'Tên hiển thị'}")
            print("  " + "-" * 45)
            for i, p in enumerate(profiles):
                print(f"  {i:<3} {p['dir']:<15} {p['display']}")
            print()
            choice = input("  Nhập số hoặc tên folder profile muốn dùng: ").strip()
            if choice.isdigit():
                idx = int(choice)
                if 0 <= idx < len(profiles):
                    profile_dir = profiles[idx]['dir']
                else:
                    print("[ERROR] Số không hợp lệ")
                    sys.exit(1)
            else:
                profile_dir = choice

    # Validate profile tồn tại
    profile_path = Path(CHROME_USER_DATA) / profile_dir
    if not profile_path.exists():
        print(f"[ERROR] Profile folder không tồn tại: {profile_path}")
        sys.exit(1)

    print(f"\n[OK] Profile đã chọn: {profile_dir!r}")

    # --- Kiểm tra debug port đã chạy chưa ---
    if is_debug_chrome_running():
        print(f"\n[INFO] Chrome debug port {DEBUG_PORT} đã đang chạy!")
        print("  Không cần làm gì thêm — script detect_game_panels.py có thể kết nối ngay.")
        print_open_tabs()
        if args.scan:
            _run_scan()
        return

    # --- Kill Chrome cũ ---
    if not args.no_kill:
        print(f"\n[STEP 1] Đóng Chrome hiện tại...")
        print("  ⚠️  Lưu ý: Mọi tab đang mở sẽ bị đóng!")
        confirm = input("  Tiếp tục? (y/N): ").strip().lower()
        if confirm != 'y':
            print("  Đã hủy.")
            sys.exit(0)
        kill_chrome()
    else:
        print("\n[SKIP] Bỏ qua bước kill Chrome (--no-kill)")

    # --- Launch Chrome ---
    print(f"\n[STEP 2] Mở Chrome với debug port {DEBUG_PORT}...")
    launch_chrome(profile_dir, chrome_exe, url=args.url)

    # --- Chờ port sẵn sàng ---
    if not wait_for_debug_port():
        print("\n[ERROR] Chrome không phản hồi trên debug port!")
        print("  Thử:")
        print("  1. Chạy lại script")
        print("  2. Kiểm tra xem có Chrome nào khác đang dùng port 9222 không")
        sys.exit(1)

    # --- In tabs đang mở ---
    time.sleep(1)  # Chờ thêm cho tab restore
    print_open_tabs()

    print("\n" + "=" * 60)
    print("✅ Chrome debug đang chạy!")
    print(f"   Profile: {profile_dir}")
    print(f"   Debug URL: http://localhost:{DEBUG_PORT}/json")
    print()
    print("📌 Tiếp theo:")
    print("   1. Mở game Sprunki trong Chrome (nếu chưa mở)")
    print("   2. Bấm Run hoặc Re-Run trên game, chờ 3-5 giây")
    print("   3. Chạy: python detect_game_panels.py")
    print("=" * 60)

    if args.scan:
        input("\n  Nhấn Enter sau khi đã vào game để bắt đầu quét...")
        _run_scan()


def _run_scan():
    scan_script = Path(__file__).parent / "detect_game_panels.py"
    if scan_script.exists():
        print(f"\n[SCAN] Chạy {scan_script}...")
        subprocess.run([sys.executable, str(scan_script)])
    else:
        print(f"[WARN] Không tìm thấy detect_game_panels.py")


if __name__ == "__main__":
    main()

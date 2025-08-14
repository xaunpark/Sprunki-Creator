# runtime_hook.py
import sys
import io

# Khi chạy dưới dạng --windowed, sys.stdin và sys.stdout không tồn tại.
# Điều này gây lỗi cho các lệnh input() hoặc print().
# Chúng ta tạo ra các đối tượng giả để chương trình không bị crash.
if sys.stdin is None:
    sys.stdin = io.StringIO() # Đối tượng giả cho input, luôn trả về chuỗi rỗng.

if sys.stdout is None:
    sys.stdout = io.StringIO() # Chuyển hướng print vào một đối tượng giả.
    
if sys.stderr is None:
    sys.stderr = io.StringIO() # Chuyển hướng lỗi vào một đối tượng giả.
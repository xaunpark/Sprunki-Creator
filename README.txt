Mở Chrome Debug - Đăng nhập conanghaimuoi@ - mail khôi phục xaunpark@
Đã cài đặt AdBlock

- Powershell by admin

& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="I:\VS-Project\Sprunki\chrome_debug_profile"

& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\Sprunki Studio\Sprunki-Creator\chrome_debug_profile"

& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\Sprunki Studio\Sprunki Creator\chrome_debug_profile"

& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\Sprunki Studio\Sprunki Creator\chrome_permanent_profile"
----
2. RDP

- Đăng nhập 127.0.0.2 cùng acc
- Vào Regedit (Registry): LÀM TƯƠNG TỰ CHO CẢ 2 CÁI SAU
HKEY_CURRENT_USER\Software\Microsoft\Terminal Server Client
HKEY_LOCAL_MACHINE\Software\Microsoft\Terminal Server Client

Click vào key Terminal Server Client ở menu bên trái.
Ở khung bên phải, chuột phải vào một khoảng trống, chọn New > DWORD (32-bit) Value.
Đặt tên cho giá trị mới này là: RemoteDesktop_SuppressWhenMinimized
Nháy đúp vào giá trị RemoteDesktop_SuppressWhenMinimized vừa tạo.
Trong ô Value data, nhập số 2.
Đảm bảo ô Base đang là Hexadecimal.
Nhấn OK.

----
3. OBS
Tạo Window Capture thay vì Display Capture, vẫn đặt tên 'Sprunki Capture'
Match Title chọn cái cuối cùng
----
4. Quy trình
- Đăng nhập Sprunki Account
- Vào RDP 127.0.0.2 -> Làm trong đây
- Ctrl + Alt + Del -> Chuyển sang Xaun làm việc

---

# Navigate to your project directory
cd "I:\VS-Project\Sprunki Tracker"

# Create a virtual environment named 'venv'
python -m venv venv

# Activate the virtual environment
.\venv\Scripts\Activate

----

1. Quay về commit cũ và xoá luôn lịch sử sau đó
git checkout main
git reset --hard <commit-id>

Nếu muốn đẩy lên remote:
git push origin main --force

2. Quay về commit cũ nhưng vẫn giữ lịch sử (an toàn hơn)

git checkout main
git revert <commit-id>..HEAD
-----

Access settings:

Your VPS name is: vps-551a2bde.vps.ovh.net

  - IPv4 address: 57.129.74.201
  - IPv6 address: 2001:41d0:701:1100::7434
  - Username:  ubuntu

I123!@#

---
databasemart
administrator
a@df243as

--GitHub
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

---
ĐỌC  CHO THÔNG NÃO

1. Trên PC cá nhân
I:\VS-Project

Folder 'Sprunki' chính là 'Sprunki Creator' trên GitHub
Folder 'Sprunki Studio' là 'Sprunki Tracker' trên GitHub

folder 'Sprunki Tracker' là bản cũ, bỏ đi cũng được. Sprunki Studio lấy code của nó them file để kết nối với Sprunki Creator.



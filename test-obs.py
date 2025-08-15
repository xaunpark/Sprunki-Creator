# test_obs_connection.py
import json
import websocket
import hashlib
import base64
import time

# --- CẤU HÌNH ---
# Hãy đảm bảo các thông số này khớp với file config.json và OBS của bạn
OBS_HOST = "192.168.1.17"  # Lấy từ config.json
OBS_PORT = 4455            # Lấy từ config.json
OBS_PASSWORD = "BDdtMRgQabXu3sy1" # Lấy từ config.json

# --- KẾT THÚC CẤU HÌNH ---

def test_obs_connection():
    """
    Một script độc lập để kiểm tra kết nối và gửi lệnh tới OBS.
    """
    ws = None
    try:
        # --- BƯỚC 1: KẾT NỐI ---
        print(f"--- BƯỚC 1: Đang kết nối tới OBS tại ws://{OBS_HOST}:{OBS_PORT}...")
        ws_url = f"ws://{OBS_HOST}:{OBS_PORT}"
        ws = websocket.create_connection(ws_url, timeout=5)
        print("✅ Kết nối WebSocket đã được thiết lập!")

        # --- BƯỚC 2: XÁC THỰC ---
        print("\n--- BƯỚC 2: Đang xác thực với OBS...")
        hello_msg = json.loads(ws.recv())

        if hello_msg.get("op") != 0:
            print(f"❌ Lỗi: Không nhận được tin nhắn 'Hello' từ OBS. Phản hồi: {hello_msg}")
            return

        print("   -> Đã nhận 'Hello' từ OBS.")
        auth_data = hello_msg.get("d", {}).get("authentication")
        if not auth_data:
            print("✅ OBS không yêu cầu mật khẩu. Bỏ qua xác thực.")
        else:
            print("   -> OBS yêu cầu mật khẩu. Bắt đầu quá trình xác thực...")
            salt = auth_data["salt"]
            challenge = auth_data["challenge"]
            
            secret = base64.b64encode(
                hashlib.sha256((OBS_PASSWORD + salt).encode()).digest()
            ).decode()
            
            auth_string = base64.b64encode(
                hashlib.sha256((secret + challenge).encode()).digest()
            ).decode()

            identify_msg = {
                "op": 1,
                "d": {
                    "rpcVersion": 1,
                    "authentication": auth_string
                }
            }
            ws.send(json.dumps(identify_msg))
            print("   -> Đã gửi yêu cầu 'Identify'...")

            identified_msg = json.loads(ws.recv())
            if identified_msg.get("op") != 2:
                print(f"❌ Lỗi Xác thực! OBS từ chối kết nối. Phản hồi: {identified_msg}")
                return
            print("✅ Xác thực thành công!")

        # --- BƯỚC 3: KIỂM TRA PHIÊN BẢN (Lệnh đọc) ---
        print("\n--- BƯỚC 3: Gửi lệnh đơn giản để lấy phiên bản (GetVersion)...")
        version_request = {
            "op": 6,
            "d": {
                "requestType": "GetVersion",
                "requestId": "get-version-test"
            }
        }
        ws.send(json.dumps(version_request))
        version_response = json.loads(ws.recv())

        print("\n--- PHẢN HỒI TỪ OBS (GetVersion) ---")
        print(json.dumps(version_response, indent=2))
        print("--------------------------------------")

        if version_response.get("d", {}).get("requestStatus", {}).get("result"):
             obs_version = version_response.get("d", {}).get("responseData", {}).get("obsVersion", "N/A")
             ws_version = version_response.get("d", {}).get("responseData", {}).get("obsWebSocketVersion", "N/A")
             print(f"✅ Lệnh GetVersion thành công! OBS: {obs_version}, WebSocket: {ws_version}")
             if not ws_version.startswith("5."):
                 print("\n⚠️  CẢNH BÁO: Phiên bản WebSocket không phải là 5.x. Đây có thể là nguyên nhân chính gây lỗi!")
        else:
            print("❌ Lệnh GetVersion thất bại. Đây là dấu hiệu của việc không tương thích phiên bản hoặc lỗi quyền.")


        # --- BƯỚC 4: THỬ LẠI LỆNH GÂY LỖI (Lệnh ghi) ---
        print("\n--- BƯỚC 4: Thử gửi lại lệnh gây lỗi (SetVideoSettings)...")
        reset_canvas_request = {
            "op": 6,
            "d": {
                "requestType": "SetVideoSettings",
                "requestId": "set-video-test",
                "requestData": {
                    "baseWidth": 1920,
                    "baseHeight": 1080
                }
            }
        }
        ws.send(json.dumps(reset_canvas_request))
        reset_response = json.loads(ws.recv())

        print("\n--- PHẢN HỒI TỪ OBS (SetVideoSettings) ---")
        print(json.dumps(reset_response, indent=2))
        print("-----------------------------------------")

        if reset_response.get("d", {}).get("requestStatus", {}).get("result"):
             print("✅ Lệnh SetVideoSettings thành công một cách bất ngờ! Vui lòng thử chạy lại script chính.")
        else:
            error_code = reset_response.get("d", {}).get("requestStatus", {}).get("code")
            comment = reset_response.get("d", {}).get("requestStatus", {}).get("comment")
            print(f"❌ Lệnh SetVideoSettings thất bại như dự đoán. Mã lỗi: {error_code}")
            print(f"   -> Nguyên nhân từ OBS: {comment}")

    except websocket.WebSocketTimeoutError:
        print("❌ Lỗi: Hết thời gian chờ kết nối. OBS không phản hồi.")
        print("   -> Kiểm tra xem OBS có đang chạy không.")
        print("   -> Kiểm tra IP/Port có đúng không.")
    except ConnectionRefusedError:
        print("❌ Lỗi: Kết nối bị từ chối.")
        print("   -> Kiểm tra xem server WebSocket trong OBS đã được bật chưa (Tools -> WebSocket Server Settings).")
        print("   -> Kiểm tra firewall có chặn port không.")
    except Exception as e:
        print(f"❌ Một lỗi không xác định đã xảy ra: {e}")
    finally:
        if ws:
            ws.close()
            print("\n✅ Đã đóng kết nối WebSocket.")

if __name__ == "__main__":
    test_obs_connection()
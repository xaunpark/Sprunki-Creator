import json
import base64
import hashlib
import websocket
import time
from config_manager import get_config

def check_obs_connection():
    print("="*50)
    print("[OBS WEBSOCKET CONNECTION TESTER]")
    print("="*50)
    
    config = get_config()
    obs_host = config.get('obs.host', '127.0.0.1')
    obs_port = config.get('obs.port', 4455)
    obs_password = config.get('obs.password', '')

    ws_url = f"ws://{obs_host}:{obs_port}"
    print(f"[Target OBS] {ws_url}")
    print(f"[Password Configured] {'Yes' if obs_password else 'No'}")
    print("-" * 50)
    
    try:
        print("[Connecting...]")
        start_time = time.time()
        ws = websocket.create_connection(ws_url, timeout=5)
        connect_time = time.time() - start_time
        print(f"[SUCCESS] Connected to WebSocket in {connect_time:.2f}s")
        
        print("[Authenticating...]")
        hello_msg = json.loads(ws.recv())
        
        if hello_msg.get("op") == 0:
            auth_data = hello_msg.get("d", {}).get("authentication")
            
            if auth_data:
                if not obs_password:
                    print("[ERROR] OBS requires a password, but none is configured in config.json")
                    ws.close()
                    return False
                    
                # Perform OBS v5 authentication
                secret = base64.b64encode(
                    hashlib.sha256((obs_password + auth_data["salt"]).encode()).digest()
                ).decode()
                
                auth_string = base64.b64encode(
                    hashlib.sha256((secret + auth_data["challenge"]).encode()).digest()
                ).decode()
                
                identify_msg = {
                    "op": 1,
                    "d": {
                        "rpcVersion": 1,
                        "authentication": auth_string
                    }
                }
                
                ws.send(json.dumps(identify_msg))
                identified_msg = json.loads(ws.recv())
                
                if identified_msg.get("op") == 2:
                    print("[SUCCESS] Authentication successful!")
                else:
                    print(f"[ERROR] Authentication failed!")
                    print(f"   Reason: {identified_msg}")
                    ws.close()
                    return False
            else:
                print("[SUCCESS] Authentication not required by OBS.")
        
        # Test a simple command to ensure permissions
        print("[Testing API request (GetVersion)...]")
        version_req = {
            "op": 6,
            "d": {
                "requestType": "GetVersion",
                "requestId": "test-req"
            }
        }
        ws.send(json.dumps(version_req))
        version_resp = json.loads(ws.recv())
        
        if version_resp.get("d", {}).get("requestStatus", {}).get("result"):
            obs_version = version_resp["d"]["responseData"]["obsVersion"]
            ws_version = version_resp["d"]["responseData"]["obsWebSocketVersion"]
            print(f"[SUCCESS] API Test successful!")
            print(f"   OBS Version: {obs_version}")
            print(f"   WebSocket Version: {ws_version}")
        
        ws.close()
        print("="*50)
        print("[ALL TESTS PASSED: OBS is ready to receive commands!]")
        print("="*50)
        return True
        
    except ConnectionRefusedError:
        print("[ERROR] Connection Refused.")
        print("   - Is OBS running on that IP?")
        print("   - Is the WebSocket server enabled in OBS? (Tools -> WebSocket Server Settings)")
        print("   - Is the port correct?")
    except websocket.WebSocketTimeoutException:
        print("[ERROR] Connection Timed Out.")
        print("   - Check if the IP address is correct and accessible.")
        print("   - Check if Windows Firewall is blocking port 4455.")
    except Exception as e:
        print(f"[ERROR] Unexpected exception: {e}")
        
    return False

if __name__ == "__main__":
    check_obs_connection()

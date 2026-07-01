import base64
import hashlib
import io
import json
import os
import time
from pathlib import Path

import websocket
from PIL import Image


DEFAULT_SOURCE_NAME = "Sprunki Capture"


def load_config() -> dict:
    config_path = Path(__file__).resolve().parent / "config.json"
    with config_path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def obs_auth(password: str, authentication: dict) -> str:
    secret = base64.b64encode(
        hashlib.sha256((password + authentication["salt"]).encode()).digest()
    ).decode()
    return base64.b64encode(
        hashlib.sha256((secret + authentication["challenge"]).encode()).digest()
    ).decode()


def connect_obs(config: dict):
    obs_config = config.get("obs", {})
    host = obs_config.get("host", "127.0.0.1")
    port = obs_config.get("port", 4455)
    password = obs_config.get("password", "")
    ws = websocket.create_connection(f"ws://{host}:{port}", timeout=10)
    hello = json.loads(ws.recv())
    identify = {"op": 1, "d": {"rpcVersion": 1}}
    authentication = hello.get("d", {}).get("authentication")
    if authentication:
        identify["d"]["authentication"] = obs_auth(password, authentication)
    ws.send(json.dumps(identify))
    identified = json.loads(ws.recv())
    if identified.get("op") != 2:
        raise RuntimeError(f"OBS identify failed: {identified}")
    return ws


def obs_request(ws, request_type: str, request_data: dict | None = None) -> dict:
    request_id = f"{request_type}-{time.time_ns()}"
    ws.send(json.dumps({
        "op": 6,
        "d": {
            "requestType": request_type,
            "requestId": request_id,
            "requestData": request_data or {},
        },
    }))
    while True:
        response = json.loads(ws.recv())
        data = response.get("d", {})
        if data.get("requestId") != request_id:
            continue
        status = data.get("requestStatus", {})
        if not status.get("result"):
            raise RuntimeError(f"{request_type} failed: {status}")
        return data.get("responseData") or {}


def decode_obs_image(image_data: str) -> Image.Image:
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    raw = base64.b64decode(image_data)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def analyze_image(image: Image.Image) -> dict:
    pixels = image.resize((160, 90)).getdata()
    total = 0
    total_sq = 0
    max_value = 0
    count = 0
    non_black = 0
    for red, green, blue in pixels:
        value = (red + green + blue) / 3
        total += value
        total_sq += value * value
        max_value = max(max_value, red, green, blue)
        count += 1
        if max(red, green, blue) > 8:
            non_black += 1
    mean = total / max(count, 1)
    variance = max(0, total_sq / max(count, 1) - mean * mean)
    non_black_ratio = non_black / max(count, 1)
    return {
        "width": image.width,
        "height": image.height,
        "meanBrightness": round(mean, 2),
        "stdBrightness": round(variance ** 0.5, 2),
        "maxChannel": int(max_value),
        "nonBlackRatio": round(non_black_ratio, 4),
        "looksBlack": mean < 3 and max_value < 12 and non_black_ratio < 0.01,
    }


def get_screenshot(ws, source_name: str, out_path: Path) -> dict:
    response = obs_request(ws, "GetSourceScreenshot", {
        "sourceName": source_name,
        "imageFormat": "png",
        "imageWidth": 1280,
        "imageHeight": 720,
        "imageCompressionQuality": 100,
    })
    image = decode_obs_image(response["imageData"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    analysis = analyze_image(image)
    analysis["path"] = str(out_path)
    return analysis


def main() -> int:
    source_name = os.getenv("OBS_VALIDATE_SOURCE", DEFAULT_SOURCE_NAME)
    output_dir = Path(os.getenv("OBS_VALIDATE_OUTPUT_DIR", "obs_capture_validation"))
    config = load_config()
    ws = connect_obs(config)
    try:
        version = obs_request(ws, "GetVersion")
        current_scene = obs_request(ws, "GetCurrentProgramScene")["currentProgramSceneName"]
        scene_items = obs_request(ws, "GetSceneItemList", {"sceneName": current_scene}).get("sceneItems", [])
        source_item = next((item for item in scene_items if item.get("sourceName") == source_name), None)

        print("OBS capture validation")
        print(f"OBS Version: {version.get('obsVersion')} / WebSocket: {version.get('obsWebSocketVersion')}")
        print(f"Current scene: {current_scene}")
        print(f"Expected source: {source_name}")
        print(f"Source present in current scene: {'yes' if source_item else 'no'}")

        if source_item:
            print(f"Source kind: {source_item.get('inputKind') or source_item.get('sourceType')}")
            item_id = source_item.get("sceneItemId")
            enabled = obs_request(ws, "GetSceneItemEnabled", {
                "sceneName": current_scene,
                "sceneItemId": item_id,
            }).get("sceneItemEnabled")
            transform = obs_request(ws, "GetSceneItemTransform", {
                "sceneName": current_scene,
                "sceneItemId": item_id,
            }).get("sceneItemTransform")
            print(f"Source enabled: {enabled}")
            print(f"Source transform: {json.dumps(transform, ensure_ascii=False)}")
            try:
                settings = obs_request(ws, "GetInputSettings", {"inputName": source_name})
                print(f"Input kind: {settings.get('inputKind')}")
                print(f"Input settings: {json.dumps(settings.get('inputSettings'), ensure_ascii=False)}")
            except Exception as exc:
                print(f"Input settings unavailable: {exc}")

        scene_analysis = get_screenshot(ws, current_scene, output_dir / "current_scene.png")
        print(f"Scene screenshot analysis: {json.dumps(scene_analysis, ensure_ascii=False)}")

        source_analysis = None
        if source_item:
            source_analysis = get_screenshot(ws, source_name, output_dir / "sprunki_capture_source.png")
            print(f"Source screenshot analysis: {json.dumps(source_analysis, ensure_ascii=False)}")

        if scene_analysis["looksBlack"] or (source_analysis and source_analysis["looksBlack"]):
            print("RESULT: FAIL - OBS screenshot looks black.")
            return 2
        print("RESULT: PASS - OBS screenshot has visible pixels.")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    raise SystemExit(main())

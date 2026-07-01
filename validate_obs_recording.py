import base64
import hashlib
import io
import json
import os
import subprocess
import time
from pathlib import Path

import websocket
from PIL import Image

from validate_obs_capture import analyze_image, connect_obs, decode_obs_image, get_screenshot, load_config, obs_request


def ffmpeg_path() -> str:
    return os.getenv("FFMPEG", "ffmpeg")


def ffprobe_path() -> str:
    return os.getenv("FFPROBE", "ffprobe")


def probe_video(path: Path) -> dict:
    try:
        completed = subprocess.run(
            [
                ffprobe_path(),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,r_frame_rate,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if completed.returncode == 0:
            return json.loads(completed.stdout or "{}")
        return {"error": completed.stderr.strip() or completed.stdout.strip()}
    except Exception as exc:
        return {"error": str(exc)}


def extract_frame(path: Path, timestamp: float, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            str(path),
            "-frames:v",
            "1",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if completed.returncode != 0 or not out_path.exists():
        raise RuntimeError(completed.stderr[-1000:] or completed.stdout[-1000:] or "ffmpeg frame extraction failed")
    return out_path


def analyze_video(path: Path, output_dir: Path) -> dict:
    frames = []
    for index, timestamp in enumerate([0.5, 2.0, 4.0], start=1):
        frame_path = output_dir / f"recording_frame_{index}_{timestamp:.1f}s.png"
        try:
            extract_frame(path, timestamp, frame_path)
            image = Image.open(frame_path).convert("RGB")
            analysis = analyze_image(image)
            analysis["timestamp"] = timestamp
            analysis["path"] = str(frame_path)
            frames.append(analysis)
        except Exception as exc:
            frames.append({"timestamp": timestamp, "error": str(exc)})
    usable = [frame for frame in frames if "looksBlack" in frame]
    return {
        "path": str(path),
        "exists": path.exists(),
        "sizeBytes": path.stat().st_size if path.exists() else 0,
        "probe": probe_video(path),
        "frames": frames,
        "looksBlack": bool(usable) and all(frame["looksBlack"] for frame in usable),
    }


def main() -> int:
    output_dir = Path(os.getenv("OBS_VALIDATE_OUTPUT_DIR", "obs_recording_validation"))
    duration = float(os.getenv("OBS_VALIDATE_RECORD_SECONDS", "5"))
    source_name = os.getenv("OBS_VALIDATE_SOURCE", "Sprunki Capture")
    config = load_config()
    ws = connect_obs(config)
    try:
        status = obs_request(ws, "GetRecordStatus")
        if status.get("outputActive"):
            print(f"OBS is already recording: {status}")
            print("RESULT: FAIL - stop the active recording before running this smoke test.")
            return 3

        print("OBS recording validation")
        scene_before = get_screenshot(ws, obs_request(ws, "GetCurrentProgramScene")["currentProgramSceneName"], output_dir / "scene_before_record.png")
        source_before = get_screenshot(ws, source_name, output_dir / "source_before_record.png")
        print(f"Scene before recording: {json.dumps(scene_before, ensure_ascii=False)}")
        print(f"Source before recording: {json.dumps(source_before, ensure_ascii=False)}")

        obs_request(ws, "StartRecord")
        print(f"Recording for {duration:.1f}s...")
        time.sleep(duration)
        stop_response = obs_request(ws, "StopRecord")
        output_path = stop_response.get("outputPath")
        if not output_path:
            print(f"StopRecord response did not include outputPath: {stop_response}")
            print("RESULT: FAIL - could not locate recorded file.")
            return 4

        video_path = Path(output_path)
        # OBS may return before the file is fully flushed.
        for _ in range(20):
            if video_path.exists() and video_path.stat().st_size > 0:
                break
            time.sleep(0.5)

        video_analysis = analyze_video(video_path, output_dir)
        print(f"Recorded file analysis: {json.dumps(video_analysis, ensure_ascii=False)}")
        if video_analysis["looksBlack"]:
            print("RESULT: FAIL - OBS recording output is black even though source screenshot may be visible.")
            return 2
        print("RESULT: PASS - OBS recording output has visible pixels.")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    raise SystemExit(main())

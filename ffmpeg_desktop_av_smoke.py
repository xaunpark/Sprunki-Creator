import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import websocket

from headless_identity_probe import (
    CLICK_CENTER_JS,
    CLICK_RUN_JS,
    cdp_eval,
    cdp_send,
    find_chrome,
    free_port,
    inject_cookies,
    wait_debug,
    wait_document_ready,
)
from headless_play_record_smoke import (
    CLICK_FULLSCREEN_JS,
    CLICK_PROJECT_RUN_JS,
    RUNTIME_WORKFLOW_LAYOUT_JS,
    dispatch_mouse,
    measure_audio_volume,
)


def find_tab_ws(port):
    tabs = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
    page = next(tab for tab in tabs if tab.get("type") == "page")
    return page["webSocketDebuggerUrl"]


def start_ffmpeg(out_path: Path, width: int, height: int, fps: int, audio_device: str | None):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f",
        "gdigrab",
        "-framerate",
        str(fps),
        "-offset_x",
        "0",
        "-offset_y",
        "0",
        "-video_size",
        f"{width}x{height}",
        "-i",
        "desktop",
    ]
    if audio_device:
        cmd += ["-f", "dshow", "-i", f"audio={audio_device}"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
    ]
    if audio_device:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += [str(out_path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def stop_ffmpeg(proc, timeout=10):
    if proc.poll() is not None:
        stdout, stderr = proc.communicate(timeout=1)
        return {"returncode": proc.returncode, "stdout": stdout, "stderr": stderr}
    try:
        proc.stdin.write("q\n")
        proc.stdin.flush()
    except Exception:
        pass
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {"returncode": proc.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        return {"returncode": proc.returncode, "stdout": stdout, "stderr": stderr, "killed": True}


def main():
    parser = argparse.ArgumentParser(description="Visible Chrome + ffmpeg gdigrab/dshow A/V smoke test.")
    parser.add_argument("url")
    parser.add_argument("--out-dir", default=r"G:\VS-Project\Sprunki\temp\ffmpeg_desktop_av")
    parser.add_argument("--duration", type=float, default=8)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=1100)
    parser.add_argument("--cookie-file", default="cocrea_cookies.json")
    parser.add_argument("--audio-device", default="CABLE Output (VB-Audio Virtual Cable)")
    parser.add_argument("--no-audio", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_mp4 = out_dir / "ffmpeg_desktop_av.mp4"
    preview_png = out_dir / "preview_3s.png"
    summary_path = out_dir / "summary.json"

    port = free_port()
    user_data_dir = tempfile.mkdtemp(prefix="sprunki-ffmpeg-desktop-av-")
    chrome = find_chrome()
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--autoplay-policy=no-user-gesture-required",
        f"--window-size={args.width},{args.height}",
        "--window-position=0,0",
        "--force-device-scale-factor=1",
        "about:blank",
    ]

    chrome_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    ffmpeg_proc = None
    ffmpeg_result = None
    try:
        wait_debug(port, timeout=30)
        ws = websocket.create_connection(find_tab_ws(port), timeout=30)
        cdp_send(ws, "Page.enable")
        cdp_send(ws, "Runtime.enable")
        cdp_send(ws, "Network.enable")
        cdp_send(ws, "Emulation.setDeviceMetricsOverride", {"width": args.width, "height": args.height, "deviceScaleFactor": 1, "mobile": False})
        cookie_result = inject_cookies(ws, args.cookie_file)
        cdp_send(ws, "Page.navigate", {"url": args.url})
        wait_document_ready(ws, timeout=60)
        time.sleep(4)

        run_clicks = []
        for _ in range(3):
            try:
                run_clicks.append({"projectRun": cdp_eval(ws, CLICK_PROJECT_RUN_JS, timeout=8)})
                run_clicks.append({"genericRun": cdp_eval(ws, CLICK_RUN_JS, timeout=8)})
                cdp_eval(ws, CLICK_CENTER_JS, timeout=8)
            except Exception as exc:
                run_clicks.append({"error": str(exc)})
            time.sleep(2)

        try:
            fullscreen_click = cdp_eval(ws, CLICK_FULLSCREEN_JS, timeout=8)
        except Exception as exc:
            fullscreen_click = {"error": str(exc)}
        time.sleep(2)

        layout = None
        layout_errors = []
        for _ in range(3):
            try:
                layout = cdp_eval(ws, RUNTIME_WORKFLOW_LAYOUT_JS, timeout=20)
                if layout and layout.get("ok"):
                    break
            except Exception as exc:
                layout_errors.append(str(exc))
            time.sleep(2)

        audio_device = None if args.no_audio else args.audio_device
        ffmpeg_proc = start_ffmpeg(output_mp4, args.width, args.height, args.fps, audio_device)
        start_time = time.monotonic()
        events = [{"type": "ffmpeg_started", "time": 0.0, "audioDevice": audio_device}]
        time.sleep(0.6)

        icons = (layout or {}).get("icons") or []
        slots = (layout or {}).get("slots") or []
        if icons and slots:
            icon = icons[0]
            slot = slots[0]
            x1, y1 = icon["x"], icon["y"]
            x2, y2 = slot["x"], slot["y"]
            events.append({"type": "drag_mouse_pressed", "time": time.monotonic() - start_time, "x": x1, "y": y1})
            dispatch_mouse(ws, "mouseMoved", x1, y1, buttons=0)
            time.sleep(0.08)
            dispatch_mouse(ws, "mousePressed", x1, y1, buttons=1)
            time.sleep(0.12)
            for step in range(1, 21):
                t = step / 20
                dispatch_mouse(ws, "mouseMoved", x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, buttons=1)
                time.sleep(0.025)
            events.append({"type": "drag_mouse_release_before", "time": time.monotonic() - start_time, "x": x2, "y": y2})
            dispatch_mouse(ws, "mouseReleased", x2, y2, buttons=0)
            events.append({"type": "drag_mouse_released", "time": time.monotonic() - start_time, "x": x2, "y": y2})
        else:
            events.append({"type": "record_existing_state_no_icons", "time": time.monotonic() - start_time, "layoutCounts": {"icons": len(icons), "slots": len(slots)}, "layoutErrors": layout_errors})

        time.sleep(args.duration)
        ffmpeg_result = stop_ffmpeg(ffmpeg_proc)
        ffmpeg_proc = None

        ffprobe = subprocess.run(
            ["ffprobe", "-hide_banner", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name,width,height,duration,bit_rate,nb_frames,sample_rate,channels", "-show_entries", "format=duration,size", str(output_mp4)],
            capture_output=True,
            text=True,
        )
        preview_result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-y", "-ss", "00:00:03", "-i", str(output_mp4), "-frames:v", "1", "-update", "1", str(preview_png)],
            capture_output=True,
            text=True,
        )
        volume = measure_audio_volume(output_mp4) if audio_device and output_mp4.exists() else None
        silence = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(output_mp4), "-af", "silencedetect=noise=-50dB:d=0.05", "-f", "null", "NUL"],
            capture_output=True,
            text=True,
        ) if audio_device and output_mp4.exists() else None

        summary = {
            "url": args.url,
            "output": str(output_mp4),
            "outputBytes": output_mp4.stat().st_size if output_mp4.exists() else 0,
            "preview": str(preview_png) if preview_png.exists() else None,
            "cookieResult": cookie_result,
            "runClicks": run_clicks,
            "fullscreenClick": fullscreen_click,
            "layoutCounts": {"icons": len((layout or {}).get("icons") or []), "slots": len((layout or {}).get("slots") or [])},
            "events": events,
            "ffmpegReturncode": ffmpeg_result.get("returncode") if ffmpeg_result else None,
            "ffmpegStderrTail": (ffmpeg_result.get("stderr") or "")[-4000:] if ffmpeg_result else None,
            "ffprobe": ffprobe.stdout,
            "previewReturncode": preview_result.returncode,
            "audioVolume": volume,
            "silencedetect": "\n".join(line for line in f"{silence.stdout}\n{silence.stderr}".splitlines() if "silence_" in line) if silence else None,
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    finally:
        if ffmpeg_proc:
            try:
                stop_ffmpeg(ffmpeg_proc, timeout=5)
            except Exception:
                pass
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        try:
            chrome_proc.terminate()
            chrome_proc.wait(timeout=5)
        except Exception:
            try:
                chrome_proc.kill()
            except Exception:
                pass
        shutil.rmtree(user_data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

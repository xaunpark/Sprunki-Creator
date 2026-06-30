import argparse
import base64
import json
import os
import shutil
import socket
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


MEDIARECORDER_AV_HOOK_JS = r"""
(() => {
  window.__sprunkiAVRecorder = {
    installed: false,
    errors: [],
    events: [],
    chunks: [],
    contexts: 0,
    destinations: [],
    recorder: null,
    startedAt: null,
    stoppedAt: null,
    streamInfo: null
  };

  const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
  if (!NativeAudioContext) {
    window.__sprunkiAVRecorder.errors.push('AudioContext unavailable');
  } else {
    function wrapContext(ctx) {
      try {
        if (ctx.__sprunkiAVWrapped) return ctx;
        ctx.__sprunkiAVWrapped = true;
        window.__sprunkiAVRecorder.contexts += 1;
        const dest = ctx.createMediaStreamDestination();
        ctx.__sprunkiAVDestination = dest;
        window.__sprunkiAVRecorder.destinations.push(dest);

        const nativeConnect = AudioNode.prototype.connect;
        if (!AudioNode.prototype.__sprunkiAVPatched) {
          AudioNode.prototype.connect = function(destination, ...args) {
            const result = nativeConnect.call(this, destination, ...args);
            try {
              const context = this.context;
              if (context && context.__sprunkiAVDestination && destination === context.destination) {
                nativeConnect.call(this, context.__sprunkiAVDestination);
              }
            } catch (error) {
              window.__sprunkiAVRecorder.errors.push(String(error));
            }
            return result;
          };
          AudioNode.prototype.__sprunkiAVPatched = true;
        }
      } catch (error) {
        window.__sprunkiAVRecorder.errors.push(String(error));
      }
      return ctx;
    }

    function WrappedAudioContext(...args) {
      return wrapContext(new NativeAudioContext(...args));
    }
    WrappedAudioContext.prototype = NativeAudioContext.prototype;
    Object.setPrototypeOf(WrappedAudioContext, NativeAudioContext);
    window.AudioContext = WrappedAudioContext;
    if (window.webkitAudioContext) window.webkitAudioContext = WrappedAudioContext;
  }

  window.__sprunkiAVRecorder.start = async (fps = 30) => {
    const state = window.__sprunkiAVRecorder;
    state.chunks = [];
    const canvas = Array.from(document.querySelectorAll('canvas'))
      .map((node, index) => {
        const rect = node.getBoundingClientRect();
        return {node, index, rect, area: rect.width * rect.height};
      })
      .sort((a, b) => b.area - a.area)[0];
    if (!canvas?.node?.captureStream) {
      return {ok:false, error:'canvas.captureStream unavailable', errors: state.errors};
    }

    const videoStream = canvas.node.captureStream(fps);
    for (const track of videoStream.getVideoTracks()) {
      if (typeof track.requestFrame === 'function') {
        const intervalMs = Math.max(33, Math.floor(1000 / Math.max(1, fps)));
        const timer = setInterval(() => {
          try {
            if (track.readyState === 'live') track.requestFrame();
          } catch (error) {
            state.errors.push(String(error));
          }
        }, intervalMs);
        state.requestFrameTimer = timer;
      }
    }
    const audioTracks = [];
    for (const dest of state.destinations || []) {
      for (const track of dest.stream.getAudioTracks()) audioTracks.push(track);
    }
    const tracks = [...videoStream.getVideoTracks(), ...audioTracks];
    const mixed = new MediaStream(tracks);
    const mimeTypes = [
      'video/webm;codecs=vp9,opus',
      'video/webm;codecs=vp8,opus',
      'video/webm'
    ];
    const mimeType = mimeTypes.find(type => MediaRecorder.isTypeSupported(type)) || '';
    const recorder = new MediaRecorder(mixed, mimeType ? {mimeType} : undefined);
    state.events.push({type:'recorder_created', state: recorder.state, mimeType: recorder.mimeType, at: Date.now()});
    recorder.onstart = () => state.events.push({type:'start', state: recorder.state, at: Date.now()});
    recorder.onpause = () => state.events.push({type:'pause', state: recorder.state, at: Date.now()});
    recorder.onresume = () => state.events.push({type:'resume', state: recorder.state, at: Date.now()});
    recorder.onstop = () => state.events.push({type:'stop_event', state: recorder.state, at: Date.now()});
    recorder.ondataavailable = async event => {
      state.events.push({type:'dataavailable', size: event.data ? event.data.size : 0, at: Date.now()});
      if (event.data && event.data.size > 0) {
        const buffer = await event.data.arrayBuffer();
        const bytes = Array.from(new Uint8Array(buffer));
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        state.chunks.push(btoa(binary));
      }
    };
    recorder.onerror = event => state.errors.push(String(event.error || event));
    recorder.start(1000);
    state.events.push({type:'start_called', state: recorder.state, at: Date.now()});
    state.recorder = recorder;
    state.startedAt = new Date().toISOString();
    state.streamInfo = {
      mimeType: recorder.mimeType,
      canvas: {
        index: canvas.index,
        width: canvas.node.width,
        height: canvas.node.height,
        rectWidth: canvas.rect.width,
        rectHeight: canvas.rect.height
      },
      videoTracks: videoStream.getVideoTracks().map(track => ({label: track.label, readyState: track.readyState, enabled: track.enabled})),
      audioTrackCount: audioTracks.length,
      totalTrackCount: tracks.length
    };
    return {ok:true, startedAt: state.startedAt, streamInfo: state.streamInfo, errors: state.errors};
  };

  window.__sprunkiAVRecorder.stop = () => new Promise(resolve => {
    const state = window.__sprunkiAVRecorder;
    const recorder = state.recorder;
    if (!recorder) return resolve({ok:false, error:'recorder missing', chunks: state.chunks, errors: state.errors});
    function done(extra = {}) {
      state.stoppedAt = new Date().toISOString();
      if (state.requestFrameTimer) {
        clearInterval(state.requestFrameTimer);
        state.requestFrameTimer = null;
      }
      setTimeout(() => resolve({ok:true, chunks: state.chunks, chunkCount: state.chunks.length, events: state.events, streamInfo: state.streamInfo, errors: state.errors, stoppedAt: state.stoppedAt, ...extra}), 1000);
    }
    recorder.onstop = () => {
      done();
    };
    try {
      if (recorder.state !== 'inactive') recorder.requestData();
    } catch (error) {
      state.errors.push(String(error));
    }
    setTimeout(() => {
      if (recorder.state !== 'inactive') recorder.stop();
      else done();
    }, 250);
    setTimeout(() => done({timeoutFallback:true}), 3500);
  });

  window.__sprunkiAVRecorder.installed = true;
})();
"""


START_AV_JS = "window.__sprunkiAVRecorder?.start?.(%FPS%)"
STOP_AV_JS = "window.__sprunkiAVRecorder?.stop?.()"


def write_chunks(path: Path, chunks):
    with open(path, "wb") as handle:
        for chunk in chunks or []:
            handle.write(base64.b64decode(chunk))


def find_tab_ws(port):
    tabs = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
    page = next(tab for tab in tabs if tab.get("type") == "page")
    return page["webSocketDebuggerUrl"]


def main():
    parser = argparse.ArgumentParser(description="Headless Sprunki in-browser MediaRecorder A/V smoke test.")
    parser.add_argument("url")
    parser.add_argument("--out-dir", default=r"G:\VS-Project\Sprunki\temp\headless_mediarecorder_av")
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--cookie-file", default="cocrea_cookies.json")
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_webm = out_dir / "mediarecorder_av.webm"
    preview_png = out_dir / "preview_3s.png"
    summary_path = out_dir / "summary.json"

    port = free_port()
    user_data_dir = tempfile.mkdtemp(prefix="sprunki-mediarecorder-av-")
    chrome = find_chrome()
    headless_arg = [] if args.no_headless else ["--headless=new"]
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1280,1100",
        *headless_arg,
        "about:blank",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        wait_debug(port, timeout=30)
        ws = websocket.create_connection(find_tab_ws(port), timeout=30)
        cdp_send(ws, "Page.enable")
        cdp_send(ws, "Runtime.enable")
        cdp_send(ws, "Network.enable")
        cdp_send(ws, "Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
        cdp_send(ws, "Page.addScriptToEvaluateOnNewDocument", {"source": MEDIARECORDER_AV_HOOK_JS})
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
        icons = (layout or {}).get("icons") or []
        slots = (layout or {}).get("slots") or []
        if not slots:
            raise RuntimeError(f"missing slots: {layout}; errors={layout_errors}")

        start_av = cdp_eval(ws, START_AV_JS.replace("%FPS%", str(args.fps)), timeout=8)
        events = [{"type": "av_started", "time": 0.0, "result": start_av}]
        start_time = time.monotonic()

        if icons:
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
            events.append({"type": "record_existing_state_no_icons", "time": time.monotonic() - start_time, "counts": (layout or {}).get("counts")})

        time.sleep(args.duration)
        stop_av = cdp_eval(ws, STOP_AV_JS, timeout=10)
        chunks = (stop_av or {}).get("chunks") or []
        write_chunks(output_webm, chunks)

        volume = measure_audio_volume(output_webm) if output_webm.exists() and output_webm.stat().st_size else None
        ffprobe = subprocess.run(
            ["ffprobe", "-hide_banner", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name,width,height,duration,bit_rate,nb_frames,sample_rate,channels", "-show_entries", "format=duration,size", str(output_webm)],
            capture_output=True,
            text=True,
        )
        preview_result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-y", "-ss", "00:00:03", "-i", str(output_webm), "-frames:v", "1", "-update", "1", str(preview_png)],
            capture_output=True,
            text=True,
        )
        silence = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(output_webm), "-af", "silencedetect=noise=-50dB:d=0.05", "-f", "null", "NUL"],
            capture_output=True,
            text=True,
        )

        summary = {
            "url": args.url,
            "headless": not args.no_headless,
            "cookieResult": cookie_result,
            "runClicks": run_clicks,
            "fullscreenClick": fullscreen_click,
            "layoutCounts": {"icons": len(icons), "slots": len(slots)},
            "events": events,
            "startAV": start_av,
            "stopAV": {k: v for k, v in (stop_av or {}).items() if k != "chunks"},
            "output": str(output_webm),
            "outputBytes": output_webm.stat().st_size if output_webm.exists() else 0,
            "preview": str(preview_png) if preview_png.exists() else None,
            "audioVolume": volume,
            "ffprobe": ffprobe.stdout,
            "previewReturncode": preview_result.returncode,
            "silencedetect": "\n".join(line for line in f"{silence.stdout}\n{silence.stderr}".splitlines() if "silence_" in line),
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(user_data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

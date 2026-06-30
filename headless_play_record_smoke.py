import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
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


AUDIO_HOOK_JS = r"""
(() => {
  window.__sprunkiAudioProbe = {
    installed: false,
    contexts: 0,
    destinations: [],
    chunks: [],
    chunksByRecorder: [],
    chunkEvents: [],
    recorders: [],
    errors: [],
    recording: false,
    startedAt: null
  };
  const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
  if (!NativeAudioContext) {
    window.__sprunkiAudioProbe.errors.push('AudioContext unavailable');
    return;
  }

  function wrapContext(ctx) {
    try {
      if (ctx.__sprunkiProbeWrapped) return ctx;
      ctx.__sprunkiProbeWrapped = true;
      window.__sprunkiAudioProbe.contexts += 1;
      const dest = ctx.createMediaStreamDestination();
      ctx.__sprunkiProbeDestination = dest;
      window.__sprunkiAudioProbe.destinations.push(dest);

      const nativeConnect = AudioNode.prototype.connect;
      if (!AudioNode.prototype.__sprunkiProbePatched) {
        AudioNode.prototype.connect = function(destination, ...args) {
          const result = nativeConnect.call(this, destination, ...args);
          try {
            const context = this.context;
            if (context && context.__sprunkiProbeDestination && destination === context.destination) {
              nativeConnect.call(this, context.__sprunkiProbeDestination);
            }
          } catch (error) {
            window.__sprunkiAudioProbe.errors.push(String(error));
          }
          return result;
        };
        AudioNode.prototype.__sprunkiProbePatched = true;
      }
      if (window.__sprunkiAudioProbe.recording) window.__sprunkiAudioProbe.start();
    } catch (error) {
      window.__sprunkiAudioProbe.errors.push(String(error));
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
  window.__sprunkiAudioProbe.start = () => {
    const probe = window.__sprunkiAudioProbe;
    probe.chunks = [];
    probe.chunksByRecorder = [];
    probe.chunkEvents = [];
    for (const recorder of probe.recorders || []) {
      try {
        if (recorder.state !== 'inactive') recorder.stop();
      } catch (error) {
        probe.errors.push(String(error));
      }
    }
    probe.recorders = [];
    probe.recording = true;
    probe.startedAt = new Date().toISOString();
    for (const dest of probe.destinations || []) {
      try {
        const recorderIndex = probe.recorders.length;
        const recorderChunks = [];
        probe.chunksByRecorder.push(recorderChunks);
        const recorder = new MediaRecorder(dest.stream, {mimeType: 'audio/webm'});
        recorder.ondataavailable = async event => {
          if (event.data && event.data.size > 0) {
            const buffer = await event.data.arrayBuffer();
            const bytes = Array.from(new Uint8Array(buffer));
            let binary = '';
            for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
            const encoded = btoa(binary);
            probe.chunks.push(encoded);
            recorderChunks.push(encoded);
            probe.chunkEvents.push({recorderIndex, chunk: encoded});
          }
        };
        recorder.onerror = event => probe.errors.push(String(event.error || event));
        recorder.start(1000);
        probe.recorders.push(recorder);
      } catch (error) {
        probe.errors.push(String(error));
      }
    }
    return {ok:true, contexts: probe.contexts, destinations: probe.destinations.length, recorders: probe.recorders.length, startedAt: probe.startedAt};
  };
  window.__sprunkiAudioProbe.installed = true;
})();
"""


START_AUDIO_JS = r"""
(() => {
  const probe = window.__sprunkiAudioProbe;
  if (!probe?.start) return {ok:false, error:'audio probe missing'};
  return probe.start();
})();
"""


STOP_AUDIO_JS = r"""
new Promise(resolve => {
  const probe = window.__sprunkiAudioProbe;
  if (!probe) return resolve({ok:false, error:'audio probe missing'});
  let pending = 0;
  for (const recorder of probe.recorders || []) {
    try {
      if (recorder.state !== 'inactive') {
        pending++;
        recorder.onstop = () => {
          pending--;
          if (pending <= 0) resolve({ok:true, chunks: probe.chunks, contexts: probe.contexts, errors: probe.errors});
        };
        recorder.stop();
      }
    } catch (error) {
      probe.errors.push(String(error));
    }
  }
  probe.recording = false;
  setTimeout(() => resolve({ok:true, chunks: probe.chunks, chunkEvents: probe.chunkEvents, contexts: probe.contexts, errors: probe.errors}), 2500);
});
"""

CLICK_PROJECT_RUN_JS = r"""
(() => {
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'))
    .map(el => {
      const rect = el.getBoundingClientRect();
      const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
      return {el, rect, text};
    })
    .filter(item => item.rect.width > 30 && item.rect.height > 20 && /^run$/i.test(item.text));
  candidates.sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
  const item = candidates[0];
  if (!item) return {clicked:false, candidateCount:0};
  const x = item.rect.left + item.rect.width / 2;
  const y = item.rect.top + item.rect.height / 2;
  for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
    item.el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, clientX:x, clientY:y}));
  }
  return {clicked:true, candidateCount:candidates.length, text:item.text, x, y, width:item.rect.width, height:item.rect.height};
})();
"""

CLICK_FULLSCREEN_JS = r"""
(() => {
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], svg, div, span'))
    .filter(el => {
      const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase();
      const cls = String(el.className || '').toLowerCase();
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0 || rect.width > 220 || rect.height > 140) return false;
      return text.includes('full screen') || text.includes('fullscreen') || cls.includes('full-screen') || cls.includes('fullscreen');
    });
  candidates.sort((a, b) => {
    const ar = a.getBoundingClientRect();
    const br = b.getBoundingClientRect();
    return (br.top - ar.top) || ((ar.width * ar.height) - (br.width * br.height));
  });
  for (const el of candidates) {
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, clientX:rect.left+rect.width/2, clientY:rect.top+rect.height/2}));
      el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:rect.left+rect.width/2, clientY:rect.top+rect.height/2}));
      el.click();
      return {clicked:true, text:(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0,80), className:String(el.className || '').slice(0,120)};
    }
  }
  const canvas = document.querySelector('canvas');
  if (canvas?.requestFullscreen) {
    canvas.requestFullscreen().catch(() => {});
    return {clicked:false, requestedFullscreen:true, target:'canvas'};
  }
  const target = canvas?.closest('[class*="stage"], [class*="player"], [class*="project"]') || canvas || document.documentElement;
  if (target?.requestFullscreen) {
    target.requestFullscreen().catch(() => {});
    return {clicked:false, requestedFullscreen:true, candidateCount:candidates.length};
  }
  return {clicked:false, requestedFullscreen:false, candidateCount:candidates.length};
})();
"""

CAPTURE_CANVAS_FRAME_JS = r"""
(() => {
  const canvas = Array.from(document.querySelectorAll('canvas'))
    .map((node, index) => {
      const rect = node.getBoundingClientRect();
      return {node, index, rect, area: rect.width * rect.height};
    })
    .sort((a, b) => b.area - a.area)[0];
  if (!canvas || !canvas.node.width || !canvas.node.height) return {ok:false, error:'canvas_not_found'};
  try {
    return {
      ok: true,
      index: canvas.index,
      width: canvas.node.width,
      height: canvas.node.height,
      rectWidth: canvas.rect.width,
      rectHeight: canvas.rect.height,
      dataUrl: canvas.node.toDataURL('image/png')
    };
  } catch (error) {
    return {ok:false, error:String(error)};
  }
})();
"""

CANVAS_RECT_JS = r"""
(() => {
  const canvases = Array.from(document.querySelectorAll('canvas')).map((canvas, index) => {
    const rect = canvas.getBoundingClientRect();
    return {
      index,
      width: canvas.width,
      height: canvas.height,
      x: rect.x,
      y: rect.y,
      rectWidth: rect.width,
      rectHeight: rect.height,
      area: rect.width * rect.height,
      className: String(canvas.className || '').slice(0, 120)
    };
  }).sort((a, b) => b.area - a.area);
  return {
    fullscreen: !!document.fullscreenElement,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio,
    canvas: canvases[0] || null,
    canvases
  };
})();
"""

RUNTIME_WORKFLOW_LAYOUT_JS = r"""
(() => {
  function findVM() {
    if (window.__vm?.runtime) return window.__vm;
    if (window.Scratch?.vm?.runtime) return window.Scratch.vm;
    function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        const reactKey = getReactKey(el);
        if (!reactKey) continue;
        let fiber = el[reactKey];
        let depth = 0;
        while (fiber && depth++ < 180) {
          const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
          if (props?.vm?.runtime) {
            window.__vm = props.vm;
            return props.vm;
          }
          fiber = fiber.return;
        }
      }
    }
    return null;
  }

  const vm = findVM();
  const canvas = Array.from(document.querySelectorAll('canvas'))
    .map((node, index) => {
      const rect = node.getBoundingClientRect();
      return {node, index, rect, area: rect.width * rect.height};
    })
    .sort((a, b) => b.area - a.area)[0];
  if (!vm || !canvas) return {ok:false, error: !vm ? 'vm_not_found' : 'canvas_not_found'};

  const rect = canvas.rect;
  const stageW = 640;
  const stageH = 360;
  function toClient(logicX, logicY) {
    return {
      x: rect.left + rect.width / 2 + (Number(logicX) || 0) * rect.width / stageW,
      y: rect.top + rect.height / 2 - (Number(logicY) || 0) * rect.height / stageH
    };
  }

  function costumeName(target) {
    const costumes = target.sprite?.costumes || [];
    return costumes[target.currentCostume]?.name || null;
  }

  function listOrVarCount(names) {
    const wanted = names.map(n => n.toLowerCase());
    for (const target of vm.runtime.targets || []) {
      for (const variable of Object.values(target.variables || {})) {
        if (!wanted.includes(String(variable.name || '').toLowerCase())) continue;
        const value = variable.value;
        if (Array.isArray(value)) return value.filter(v => String(v) === '1' || v === 1 || v === true).length;
        if (typeof value === 'number') return value;
        const numeric = Number(value);
        if (Number.isFinite(numeric)) return numeric;
      }
    }
    return null;
  }

  const allTargets = (vm.runtime.targets || []).filter(t => !t.isStage);
  const icons = allTargets
    .map((target, runtimeIndex) => {
      const name = target.sprite?.name || target.getName?.() || '';
      const point = toClient(target.x, target.y);
      return {
        runtimeIndex,
        name,
        visible: !!target.visible,
        logicX: target.x,
        logicY: target.y,
        x: point.x,
        y: point.y,
        costumeName: costumeName(target)
      };
    })
    .filter(icon => icon.visible && /Icons/i.test(icon.name) && icon.y >= rect.top + rect.height * 0.50)
    .map((icon, index) => ({...icon, index}));

  const slots = allTargets
    .map((target, runtimeIndex) => {
      const name = target.sprite?.name || target.getName?.() || '';
      const point = toClient(target.x, target.y);
      return {
        runtimeIndex,
        name,
        visible: !!target.visible,
        logicX: target.x,
        logicY: target.y,
        x: point.x,
        y: point.y,
        costumeName: costumeName(target)
      };
    })
    .filter(slot => /^Polo\s+\d+$/i.test(slot.name))
    .sort((a, b) => a.x - b.x)
    .map((slot, index) => ({...slot, index}));

  return {
    ok: true,
    fullscreen: !!document.fullscreenElement,
    viewport: {width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio},
    canvas: {x: rect.x, y: rect.y, rectWidth: rect.width, rectHeight: rect.height, width: canvas.node.width, height: canvas.node.height},
    icons,
    slots,
    counts: {
      characters: listOrVarCount(['Characters']),
      putted: listOrVarCount(['Putted characters', 'Putted Characters'])
    }
  };
})();
"""


def write_frames_to_video(frame_dir: Path, output_path: Path, fps: int, frame_records=None):
    if frame_records:
        concat_path = frame_dir / "frames_concat.txt"
        lines = ["ffconcat version 1.0\n"]
        for index, record in enumerate(frame_records):
            frame_path = Path(record["path"]).resolve()
            escaped = str(frame_path).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{escaped}'\n")
            if index < len(frame_records) - 1:
                duration = max(0.001, float(frame_records[index + 1]["time"]) - float(record["time"]))
                lines.append(f"duration {duration:.6f}\n")
        if frame_records:
            last_path = Path(frame_records[-1]["path"]).resolve()
            escaped = str(last_path).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{escaped}'\n")
        concat_path.write_text("".join(lines), encoding="utf-8")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-safe",
            "0",
            "-f",
            "concat",
            "-i",
            str(concat_path),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    else:
        frame_pattern = str(frame_dir / "frame_%05d.png")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            frame_pattern,
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def mux_audio(video_path: Path, audio_path: Path, output_path: Path):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def measure_audio_volume(audio_path: Path):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "NUL",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = f"{result.stdout}\n{result.stderr}"
    volume = {"path": str(audio_path), "meanVolume": None, "maxVolume": None, "returncode": result.returncode}
    for line in output.splitlines():
        if "mean_volume:" in line:
            try:
                volume["meanVolume"] = float(line.split("mean_volume:", 1)[1].split("dB", 1)[0].strip())
            except Exception:
                pass
        if "max_volume:" in line:
            try:
                volume["maxVolume"] = float(line.split("max_volume:", 1)[1].split("dB", 1)[0].strip())
            except Exception:
                pass
    return volume


def dispatch_mouse(ws, event_type: str, x: float, y: float, button="left", buttons=1):
    cdp_send(
        ws,
        "Input.dispatchMouseEvent",
        {
            "type": event_type,
            "x": float(x),
            "y": float(y),
            "button": button,
            "buttons": buttons,
            "clickCount": 1,
        },
        timeout=5,
    )


def main():
    parser = argparse.ArgumentParser(description="Experimental headless Sprunki play/record smoke test.")
    parser.add_argument("url")
    parser.add_argument("--out-dir", default=r"G:\VS-Project\Sprunki\temp\headless_play_record_smoke")
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--cookie-file", default="cocrea_cookies.json")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--drag-count", type=int, default=3)
    parser.add_argument("--capture-source", choices=["canvas", "page"], default="canvas")
    parser.add_argument("--no-realtime-capture", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_dir = out_dir / "frames"
    if frame_dir.exists():
        shutil.rmtree(frame_dir)
    frame_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / "video_only.mp4"
    audio_path = out_dir / "audio.webm"
    muxed_path = out_dir / "headless_capture_with_audio.mp4"
    summary_path = out_dir / "summary.json"

    port = free_port()
    user_data_dir = tempfile.mkdtemp(prefix="sprunki-headless-record-")
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
    ws_capture = None
    frame_count = 0
    capture_errors = []
    frame_records = []
    recording_events = []
    recording_start_monotonic = None
    capture_lock = threading.Lock()
    control_lock = threading.Lock()
    capture_stop = threading.Event()
    realtime_capture = not args.no_realtime_capture

    def capture_frame():
        nonlocal frame_count
        try:
            capture_started = time.monotonic()
            with capture_lock:
                capture_ws = ws_capture or ws
                if args.capture_source == "canvas":
                    canvas_frame = cdp_eval(capture_ws, CAPTURE_CANVAS_FRAME_JS, timeout=10) or {}
                    if not canvas_frame.get("ok"):
                        raise RuntimeError(f"canvas capture failed: {canvas_frame}")
                    encoded = str(canvas_frame.get("dataUrl", "")).split(",", 1)[-1]
                else:
                    screenshot = cdp_send(
                        capture_ws,
                        "Page.captureScreenshot",
                        {"format": "png", "captureBeyondViewport": False},
                        timeout=10,
                    )
                    encoded = screenshot.get("result", {}).get("data", "")
                frame_count += 1
                frame_path = frame_dir / f"frame_{frame_count:05d}.png"
                frame_path.write_bytes(base64.b64decode(encoded))
                if recording_start_monotonic is not None:
                    frame_records.append({
                        "index": frame_count,
                        "time": capture_started - recording_start_monotonic,
                        "path": str(frame_path),
                    })
            return True
        except Exception as exc:
            capture_errors.append(str(exc))
            return False

    def capture_loop():
        interval = 1.0 / max(args.fps, 1)
        next_tick = time.monotonic()
        while not capture_stop.is_set():
            started = time.monotonic()
            capture_frame()
            next_tick = max(next_tick + interval, started + interval)
            wait_seconds = max(0.0, next_tick - time.monotonic())
            capture_stop.wait(wait_seconds)

    def cdp_eval_serial(expression, timeout=10):
        with control_lock:
            return cdp_eval(ws, expression, timeout=timeout)

    def dispatch_mouse_serial(event_type: str, x: float, y: float, button="left", buttons=1):
        with control_lock:
            dispatch_mouse(ws, event_type, x, y, button=button, buttons=buttons)

    audio_start_result = None
    capture_thread = None

    def start_recording_if_needed():
        nonlocal audio_start_result, capture_thread, recording_start_monotonic
        if audio_start_result is not None:
            return audio_start_result
        recording_start_monotonic = time.monotonic()
        audio_start_result = cdp_eval_serial(START_AUDIO_JS, timeout=8)
        recording_events.append({"type": "recording_started", "time": time.monotonic() - recording_start_monotonic})
        if realtime_capture:
            capture_thread = threading.Thread(target=capture_loop, daemon=True)
            capture_thread.start()
        return audio_start_result

    def drag_relative(rect, src_rel_x, src_rel_y, dst_rel_x, dst_rel_y, steps=18):
        x1 = rect["x"] + rect["rectWidth"] * src_rel_x
        y1 = rect["y"] + rect["rectHeight"] * src_rel_y
        x2 = rect["x"] + rect["rectWidth"] * dst_rel_x
        y2 = rect["y"] + rect["rectHeight"] * dst_rel_y
        dispatch_mouse(ws, "mouseMoved", x1, y1, buttons=0)
        capture_frame()
        dispatch_mouse(ws, "mousePressed", x1, y1, buttons=1)
        capture_frame()
        for step in range(1, steps + 1):
            t = step / steps
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            dispatch_mouse(ws, "mouseMoved", x, y, buttons=1)
            if step % 3 == 0:
                capture_frame()
            time.sleep(0.04)
        dispatch_mouse(ws, "mouseReleased", x2, y2, buttons=0)
        capture_frame()
        time.sleep(1.2)

    def drag_points(x1, y1, x2, y2, steps=20):
        if recording_start_monotonic is not None:
            recording_events.append({"type": "drag_move_to_start", "time": time.monotonic() - recording_start_monotonic, "x": x1, "y": y1})
        dispatch_mouse_serial("mouseMoved", x1, y1, buttons=0)
        if not realtime_capture:
            capture_frame()
        time.sleep(0.15)
        if recording_start_monotonic is not None:
            recording_events.append({"type": "drag_mouse_pressed", "time": time.monotonic() - recording_start_monotonic, "x": x1, "y": y1})
        dispatch_mouse_serial("mousePressed", x1, y1, buttons=1)
        if not realtime_capture:
            capture_frame()
        time.sleep(0.15)
        for step in range(1, steps + 1):
            t = step / steps
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            dispatch_mouse_serial("mouseMoved", x, y, buttons=1)
            if not realtime_capture and step % 3 == 0:
                capture_frame()
            time.sleep(0.035)
        if recording_start_monotonic is not None:
            recording_events.append({"type": "drag_mouse_release_before", "time": time.monotonic() - recording_start_monotonic, "x": x2, "y": y2})
        dispatch_mouse_serial("mouseReleased", x2, y2, buttons=0)
        if recording_start_monotonic is not None:
            recording_events.append({"type": "drag_mouse_released", "time": time.monotonic() - recording_start_monotonic, "x": x2, "y": y2})
        if not realtime_capture:
            capture_frame()
        time.sleep(1.0)

    def counts_changed(before, after):
        before_counts = (before or {}).get("counts") or {}
        after_counts = (after or {}).get("counts") or {}
        results = {}
        for key in ("characters", "putted"):
            b = before_counts.get(key)
            a = after_counts.get(key)
            results[key] = {
                "before": b,
                "after": a,
                "increased": isinstance(b, (int, float)) and isinstance(a, (int, float)) and a == b + 1,
            }
        return results

    def workflow_drag_icons(max_count):
        layout = cdp_eval_serial(RUNTIME_WORKFLOW_LAYOUT_JS, timeout=10)
        results = []
        if not layout or not layout.get("ok"):
            return {"layout": layout, "results": [{"ok": False, "error": "runtime_layout_unavailable"}]}
        icons = layout.get("icons") or []
        slots = layout.get("slots") or []
        if not icons or not slots:
            return {
                "layout": layout,
                "results": [{"ok": False, "error": "missing_icons_or_slots", "iconCount": len(icons), "slotCount": len(slots)}],
            }

        icon_offsets = [(0, 0), (0, -10), (0, 10), (-10, 0), (10, 0)]
        slot_offsets = [(0, 0), (0, -16), (0, 16)]
        for index in range(min(max_count, len(icons), len(slots))):
            icon = icons[index]
            slot = slots[min(index, len(slots) - 1)]
            before = cdp_eval_serial(RUNTIME_WORKFLOW_LAYOUT_JS, timeout=10)
            attempt_result = {
                "index": index,
                "icon": icon,
                "slot": slot,
                "ok": False,
                "attempts": [],
            }
            for icon_dx, icon_dy in icon_offsets:
                if attempt_result["ok"]:
                    break
                for slot_dx, slot_dy in slot_offsets:
                    start_x = icon["x"] + icon_dx
                    start_y = icon["y"] + icon_dy
                    end_x = slot["x"] + slot_dx
                    end_y = slot["y"] + slot_dy
                    start_recording_if_needed()
                    drag_points(start_x, start_y, end_x, end_y)
                    after = cdp_eval_serial(RUNTIME_WORKFLOW_LAYOUT_JS, timeout=10)
                    verification = counts_changed(before, after)
                    ok = verification["characters"]["increased"] or verification["putted"]["increased"]
                    attempt = {
                        "start": {"x": start_x, "y": start_y},
                        "end": {"x": end_x, "y": end_y},
                        "verification": verification,
                        "ok": ok,
                    }
                    attempt_result["attempts"].append(attempt)
                    if ok:
                        attempt_result["ok"] = True
                        attempt_result["successfulAttempt"] = attempt
                        break
            results.append(attempt_result)
            if realtime_capture:
                time.sleep(0.4)
            else:
                for _ in range(max(args.fps, 1)):
                    capture_frame()
                    time.sleep(1.0 / max(args.fps, 1))
        return {"layout": layout, "results": results}

    try:
        wait_debug(port, timeout=30)
        tabs = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
        page = next(tab for tab in tabs if tab.get("type") == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        ws_capture = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        cdp_send(ws, "Page.enable")
        cdp_send(ws, "Runtime.enable")
        cdp_send(ws, "Network.enable")
        cdp_send(ws_capture, "Page.enable")
        cdp_send(ws_capture, "Runtime.enable")
        cdp_send(ws, "Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
        cdp_send(ws_capture, "Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
        cdp_send(ws, "Page.addScriptToEvaluateOnNewDocument", {"source": AUDIO_HOOK_JS})
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

        fullscreen_click = cdp_eval(ws, CLICK_FULLSCREEN_JS, timeout=8)
        f11_down = cdp_send(
            ws,
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "windowsVirtualKeyCode": 122, "nativeVirtualKeyCode": 122, "key": "F11", "code": "F11"},
            timeout=5,
        )
        cdp_send(
            ws,
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "windowsVirtualKeyCode": 122, "nativeVirtualKeyCode": 122, "key": "F11", "code": "F11"},
            timeout=5,
        )
        time.sleep(2)
        canvas_before_drag = cdp_eval(ws, CANVAS_RECT_JS, timeout=8)

        capture_interval = 1.0 / max(args.fps, 1)

        workflow_drag = workflow_drag_icons(max(0, args.drag_count))
        drag_results = workflow_drag.get("results", [])
        start_recording_if_needed()

        deadline = time.time() + args.duration
        if realtime_capture:
            while time.time() < deadline:
                time.sleep(min(0.25, max(0.0, deadline - time.time())))
            capture_stop.set()
            if capture_thread:
                capture_thread.join(timeout=5)
        else:
            while time.time() < deadline:
                capture_frame()
                time.sleep(capture_interval)

        audio_result = cdp_eval(ws, STOP_AUDIO_JS, timeout=10)
        audio_chunks = (audio_result or {}).get("chunks") or []
        chunk_events = (audio_result or {}).get("chunkEvents") or []
        chunks_by_recorder_map = {}
        for event in chunk_events:
            if not isinstance(event, dict):
                continue
            recorder_index = event.get("recorderIndex")
            chunk = event.get("chunk")
            if chunk is None:
                continue
            chunks_by_recorder_map.setdefault(str(recorder_index), []).append(chunk)
        audio_candidates = []
        if chunks_by_recorder_map:
            for recorder_index, recorder_chunks in sorted(chunks_by_recorder_map.items()):
                candidate_path = out_dir / f"audio_{recorder_index}.webm"
                with open(candidate_path, "wb") as handle:
                    for chunk in recorder_chunks or []:
                        handle.write(base64.b64decode(chunk))
                if candidate_path.exists() and candidate_path.stat().st_size > 0:
                    volume = measure_audio_volume(candidate_path)
                    volume["bytes"] = candidate_path.stat().st_size
                    audio_candidates.append(volume)
        with open(audio_path, "wb") as handle:
            for chunk in audio_chunks:
                handle.write(base64.b64decode(chunk))
        if audio_path.exists() and audio_path.stat().st_size > 0:
            volume = measure_audio_volume(audio_path)
            volume["bytes"] = audio_path.stat().st_size
            volume["combined"] = True
            audio_candidates.append(volume)

        selected_audio = None
        usable_audio_candidates = [c for c in audio_candidates if isinstance(c.get("meanVolume"), (int, float))]
        if usable_audio_candidates:
            selected_audio = max(usable_audio_candidates, key=lambda item: item.get("meanVolume", -999))
        selected_audio_path = Path(selected_audio["path"]) if selected_audio else audio_path

        if frame_count:
            write_frames_to_video(frame_dir, video_path, args.fps, frame_records if realtime_capture else None)
        muxed = False
        if video_path.exists() and selected_audio_path.exists() and selected_audio_path.stat().st_size > 0:
            try:
                mux_audio(video_path, selected_audio_path, muxed_path)
                muxed = True
            except Exception as mux_error:
                muxed = False
                audio_result = audio_result or {}
                audio_result.setdefault("errors", []).append(f"mux failed: {mux_error}")

        summary = {
            "url": args.url,
            "headless": not args.no_headless,
            "captureSource": args.capture_source,
            "realtimeCapture": realtime_capture,
            "cookieResult": cookie_result,
            "runClicks": run_clicks,
            "frameCount": frame_count,
            "frameTiming": {
                "mode": "vfr_wall_clock" if realtime_capture else "constant_fps",
                "recordCount": len(frame_records),
                "firstFrameTime": frame_records[0]["time"] if frame_records else None,
                "lastFrameTime": frame_records[-1]["time"] if frame_records else None,
                "minInterval": min(
                    [frame_records[i + 1]["time"] - frame_records[i]["time"] for i in range(len(frame_records) - 1)],
                    default=None,
                ),
                "maxInterval": max(
                    [frame_records[i + 1]["time"] - frame_records[i]["time"] for i in range(len(frame_records) - 1)],
                    default=None,
                ),
            },
            "recordingEvents": recording_events,
            "videoOnly": str(video_path) if video_path.exists() else None,
            "videoOnlyBytes": video_path.stat().st_size if video_path.exists() else 0,
            "audio": str(audio_path) if audio_path.exists() else None,
            "audioBytes": audio_path.stat().st_size if audio_path.exists() else 0,
            "selectedAudio": str(selected_audio_path) if selected_audio_path.exists() else None,
            "audioCandidates": audio_candidates,
            "muxedVideo": str(muxed_path) if muxed else None,
            "muxed": muxed,
            "audioProbe": {
                "ok": (audio_result or {}).get("ok"),
                "contexts": (audio_result or {}).get("contexts"),
                "chunkCount": len(audio_chunks),
                "recorderCount": len(chunks_by_recorder_map),
                "chunkEventCount": len(chunk_events),
                "errors": (audio_result or {}).get("errors"),
            },
            "audioStart": audio_start_result,
            "fullscreenClick": fullscreen_click,
            "f11Dispatched": bool(f11_down),
            "canvasBeforeDrag": canvas_before_drag,
            "workflowLayout": workflow_drag.get("layout"),
            "dragResults": drag_results,
            "captureErrors": capture_errors[:10],
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    finally:
        if ws_capture:
            try:
                ws_capture.close()
            except Exception:
                pass
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

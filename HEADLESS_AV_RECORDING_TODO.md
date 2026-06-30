# Headless A/V Recording Notes And TODO

## Goal

Find a reliable way to record Sprunki/Cocrea gameplay video and audio without OBS, ideally in a headless/server workflow.

The key requirement is not only "video file has audio", but that audio and video are synchronized at the exact moment a character is dropped and starts playing.

## What Was Tested

### 1. Chrome headless + CDP screenshots + WebAudio recorder

Script:

- `headless_play_record_smoke.py`

Approach:

- Run Chrome with `--headless=new`.
- Control the game through CDP mouse events.
- Capture video frames through `Page.captureScreenshot` or canvas screenshots.
- Capture audio separately by hooking WebAudio and recording a `MediaStreamDestination`.
- Use ffmpeg to mux video and audio.

Result:

- Headless gameplay works.
- CDP drag/drop works.
- VM verification works:
  - `Characters` count increases after drag.
  - `Putted` count increases after drag.
- `Page.captureScreenshot` can capture visible gameplay.
- Direct canvas capture via `canvas.toDataURL()` can be black.
- Audio can be captured through WebAudio.

Problems:

- Video and audio are separate capture clocks.
- Screenshot frame timing is not constant; using `-framerate` creates a fake timeline.
- When screenshot capture and drag share one CDP connection, drag can become artificially slow.
- Even after fixing this with:
  - real wall-clock frame timestamps,
  - two CDP connections,
  - audio offset compensation,
  sync is still only approximate.

Important finding:

- With two CDP connections and VFR frame timestamps, drag timing became realistic:
  - `drag_mouse_pressed ~= 0.25s`
  - `drag_mouse_released ~= 2.17s`
- Audio started around `3.55s` before compensation.
- Applying an offset of about `1.375s` made audio start near the drop event:
  - drop/release: `2.172s`
  - audio starts: `2.1718s`

Conclusion:

- This approach can be made close, but it is not a robust production sync method.
- It depends on measured offsets and browser/audio latency behavior.

## 2. Chrome headless + in-browser MediaRecorder with shared A/V stream

Script:

- `headless_mediarecorder_av_smoke.py`

Approach:

- Use one browser-side `MediaRecorder`.
- Video source: `canvas.captureStream(fps)`.
- Audio source: WebAudio `MediaStreamDestination`.
- Combine tracks into one `MediaStream`.
- Record one `.webm` file, letting the browser timestamp A/V in one clock.

Expected advantage:

- This is the cleanest theoretical solution.
- Audio and video should be synchronized by the browser itself.
- No manual ffmpeg offset compensation needed.

Actual result in Chrome headless:

- `canvas.captureStream()` returns a live video track.
- WebAudio hook returns audio tracks.
- `MediaRecorder` starts successfully.
- But `dataavailable` events contain `size=0`.
- Output file is `0 bytes`.
- Calling `requestFrame()` on the canvas capture track did not fix it.

Conclusion:

- For this Cocrea/Sprunki renderer in Chrome headless, browser-side `MediaRecorder(canvas.captureStream + audio)` is not usable.
- This blocks the best pure-headless browser solution.

## 3. Visible Chrome + ffmpeg gdigrab/dshow

Script:

- `ffmpeg_desktop_av_smoke.py`

Approach:

- Launch normal Chrome, not headless.
- Capture visible screen region with ffmpeg `gdigrab`.
- Capture audio with DirectShow from `CABLE Output (VB-Audio Virtual Cable)`.
- Encode audio and video in the same ffmpeg process.

Result:

- Video capture works.
- Drag timing is normal.
- ffmpeg produces a valid MP4.
- Audio track exists.

Problem found during first test:

- Audio was silent because Chrome was not routed to VB-Cable yet.
- After user changed Windows sound settings:
  - Output device: `CABLE Input (VB-Audio Virtual Cable)`
  - Input device: `CABLE Output (VB-Audio Virtual Cable)`
  this route should be correct.

Important note:

- VB-Cable naming is confusing:
  - Chrome/Windows plays into `CABLE Input`.
  - ffmpeg records from `CABLE Output`.

Conclusion:

- This is a reliable A/V sync architecture because ffmpeg captures video and audio in one process.
- But it is not Chrome headless.
- It requires a real or virtual desktop surface.

## Current Technical Conclusion

Chrome headless on Windows is not enough for reliable Sprunki A/V recording.

Reasons:

- CDP screenshot/screencast has no audio.
- Browser WebAudio audio capture is separate from CDP video capture.
- Browser-side `canvas.captureStream + MediaRecorder` does not emit usable data in this headless renderer.
- Windows ffmpeg `gdigrab` needs a real desktop surface.

Reliable "headless" must mean:

> no visible UI for the user, but browser still renders into a real virtual display and audio device.

That is different from Chrome `--headless`.

## Recommended Future Solution

### Best server architecture

Use Linux virtual display/audio:

```text
Chrome normal mode
-> Xvfb or Xorg dummy display
-> PulseAudio/PipeWire null sink
-> ffmpeg captures x11grab + pulse monitor in one process
```

Benefits:

- No visible browser window.
- Browser renders normally.
- ffmpeg timestamps audio and video together.
- More production-friendly than manual screenshot/audio muxing.

Example conceptual command:

```bash
Xvfb :99 -screen 0 1280x720x24 &
export DISPLAY=:99

pulseaudio --start
pactl load-module module-null-sink sink_name=sprunki_sink
export PULSE_SINK=sprunki_sink

google-chrome --no-sandbox --window-size=1280,720 https://...

ffmpeg \
  -f x11grab -video_size 1280x720 -framerate 30 -i :99.0 \
  -f pulse -i sprunki_sink.monitor \
  -c:v libx264 -pix_fmt yuv420p \
  -c:a aac -b:a 128k \
  output.mp4
```

### Windows alternative

Use normal Chrome in a virtual desktop/session:

```text
Chrome normal mode
-> virtual display/session
-> VB-Cable audio route
-> ffmpeg gdigrab + dshow in one process
```

Requirements:

- A real captureable desktop region.
- Chrome audio routed into `CABLE Input`.
- ffmpeg audio input from `CABLE Output`.
- Use a capture size that fits the desktop resolution.

Example:

```powershell
ffmpeg `
  -f gdigrab -framerate 30 -offset_x 0 -offset_y 0 -video_size 1280x720 -i desktop `
  -f dshow -i audio="CABLE Output (VB-Audio Virtual Cable)" `
  -c:v libx264 -preset veryfast -pix_fmt yuv420p `
  -c:a aac -b:a 128k `
  output.mp4
```

## TODO

1. Retest `ffmpeg_desktop_av_smoke.py` after confirming Chrome audio is routed to VB-Cable.
   - Expected:
     - video visible,
     - audio not silent,
     - audio starts immediately/near-immediately after drop.

2. Add automatic preflight checks for Windows ffmpeg capture:
   - confirm desktop resolution,
   - reject capture region larger than screen,
   - list dshow audio devices,
   - warn if `CABLE Output` is missing,
   - measure input audio level before recording.

3. Add a clean Windows capture mode:
   - launch Chrome normal mode positioned at `0,0`,
   - set fixed window size,
   - start ffmpeg gdigrab+dshow,
   - run existing Sprunki automation via CDP,
   - stop ffmpeg,
   - validate output with ffprobe and volumedetect.

4. Research and prototype Linux server capture:
   - Xvfb or Xorg dummy display,
   - PulseAudio or PipeWire null sink,
   - Chrome normal mode,
   - ffmpeg x11grab+pulse.

5. Decide production direction:
   - If Windows-only: use visible/virtual desktop + VB-Cable.
   - If server scalable: use Linux virtual display/audio.
   - Avoid Chrome `--headless` for production A/V sync recording unless browser-side MediaRecorder starts producing valid data in future Chrome versions.

## Files Created During Exploration

- `headless_play_record_smoke.py`
  - CDP screenshot + WebAudio recording experiments.

- `headless_mediarecorder_av_smoke.py`
  - Pure browser MediaRecorder A/V experiment.

- `ffmpeg_desktop_av_smoke.py`
  - Normal Chrome + ffmpeg desktop/audio capture experiment.

## Current Recommendation

Do not build the final recorder around Chrome `--headless` plus manual screenshot/audio muxing.

For reliable A/V sync, build around ffmpeg capturing a real or virtual display and an audio device in the same process.


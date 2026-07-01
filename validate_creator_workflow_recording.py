import argparse
import json
import os
import socket
import sys
import time
import types
from pathlib import Path

from sprunki_game_player import SprunkiGamePlayer
from sprunki_obs_controller import SprunkiController
from validate_obs_recording import analyze_video
from validate_obs_capture import connect_obs, get_screenshot, load_config, obs_request


def install_safe_chrome_cleanup(player: SprunkiGamePlayer) -> None:
    def safe_cleanup_chrome_complete(self):
        print("SAFE cleanup: closing Chrome debug process without title-based window close...")
        try:
            if getattr(self, "session_websocket", None):
                try:
                    self.session_websocket.close()
                    print("   WebSocket connection closed")
                except Exception:
                    pass

            try:
                import psutil
                chrome_pids_to_kill = []
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    try:
                        name = proc.info.get("name") or ""
                        cmdline = proc.info.get("cmdline") or []
                        cmdline_str = " ".join(cmdline)
                        if "chrome" in name.lower() and f"--remote-debugging-port={self.debugging_port}" in cmdline_str:
                            chrome_pids_to_kill.append(proc.info["pid"])
                            print(f"   Found Chrome debug process: PID {proc.info['pid']}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                for pid in chrome_pids_to_kill:
                    try:
                        psutil.Process(pid).terminate()
                        print(f"   Terminated Chrome debug process: PID {pid}")
                    except Exception:
                        try:
                            psutil.Process(pid).kill()
                            print(f"   Killed Chrome debug process: PID {pid}")
                        except Exception:
                            pass

                if chrome_pids_to_kill:
                    psutil.wait_procs(
                        [psutil.Process(pid) for pid in chrome_pids_to_kill if psutil.pid_exists(pid)],
                        timeout=5,
                    )
            except Exception as exc:
                print(f"   Process cleanup warning: {exc}")
                chrome_process = getattr(self, "chrome_process", None)
                if chrome_process:
                    try:
                        chrome_process.terminate()
                        chrome_process.wait(timeout=3)
                    except Exception:
                        try:
                            chrome_process.kill()
                        except Exception:
                            pass

            print(f"   Verifying port {self.debugging_port} is released...")
            for attempt in range(10):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(("localhost", self.debugging_port))
                    sock.close()
                    if result != 0:
                        print(f"   Port {self.debugging_port} confirmed released")
                        break
                    print(f"   Port {self.debugging_port} still in use, waiting... ({attempt + 1}/10)")
                    time.sleep(1)
                except Exception as exc:
                    print(f"   Port check warning: {exc}")
                    time.sleep(1)

            self.ws_url = None
            self.chrome_process = None
            self.session_websocket = None
            self.session_websocket_url = None
            self.batch_verification_active = False
            print("SAFE cleanup completed.")
            return True
        except Exception as exc:
            print(f"SAFE cleanup error: {exc}")
            return False

    player.cleanup_chrome_complete = types.MethodType(safe_cleanup_chrome_complete, player)


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_temp_config(url: str, output_dir: Path) -> dict:
    return {
        "currentUrl": url,
        "pageTitle": "Workflow OBS validation",
        "layout": {
            "icons": [],
            "slots": [],
            "recordingArea": {
                "x": 595,
                "y": 172,
                "width": 730,
                "height": 500,
            },
        },
        "gameSettings": {
            "gameControlType": "click_drag",
        },
        "_validationOutputDir": str(output_dir),
    }


def capture_checkpoint(label: str, output_dir: Path) -> None:
    try:
        config = load_config()
        ws = connect_obs(config)
        try:
            scene = obs_request(ws, "GetCurrentProgramScene")["currentProgramSceneName"]
            scene_result = get_screenshot(ws, scene, output_dir / f"{label}_scene.png")
            print(f"[{label}] scene: {json.dumps(scene_result, ensure_ascii=False)}")
            try:
                source_result = get_screenshot(ws, "Sprunki Capture", output_dir / f"{label}_source.png")
                print(f"[{label}] source: {json.dumps(source_result, ensure_ascii=False)}")
            except Exception as exc:
                print(f"[{label}] source screenshot failed: {exc}")
        finally:
            ws.close()
    except Exception as exc:
        print(f"[{label}] OBS checkpoint failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate OBS recording through the same creator workflow setup path.")
    parser.add_argument("url", help="Cocrea/Sprunki URL to open through SprunkiGamePlayer.setup_game_environment().")
    parser.add_argument("--seconds", type=float, default=8.0, help="Recording duration after game setup.")
    parser.add_argument("--output-dir", default="creator_workflow_obs_validation")
    parser.add_argument("--cleanup", action="store_true", help="Run browser/OBS cleanup at the end. Disabled by default for debugging.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    log_handle = log_path.open("w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_handle)
    sys.stderr = Tee(sys.__stderr__, log_handle)
    print(f"Logging to: {log_path.resolve()}")

    config = write_temp_config(args.url, output_dir)
    (output_dir / "workflow_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    obs = SprunkiController()
    player = SprunkiGamePlayer()
    install_safe_chrome_cleanup(player)
    player.config = config
    player.obs_controller = obs

    try:
        print("Loading OBS config from temp workflow config...")
        if not obs.load_config_from_dict(config):
            print("RESULT: FAIL - OBS load_config_from_dict failed.")
            return 2

        print("Running OBS full_setup(use_full_screen=True)...")
        if not obs.full_setup(use_full_screen=True):
            print("RESULT: FAIL - OBS full_setup failed.")
            return 2
        capture_checkpoint("01_after_obs_full_setup", output_dir)

        filename = "creator-workflow-validation-showcase.mkv"
        print(f"Forcing OBS filename: {filename}")
        if not obs.force_obs_filename(filename):
            print("RESULT: FAIL - OBS force filename failed.")
            return 2
        capture_checkpoint("02_after_force_filename", output_dir)

        print("Running SprunkiGamePlayer.setup_game_environment(first_setup=True)...")
        if not player.setup_game_environment(first_setup=True):
            print("RESULT: FAIL - game setup failed.")
            return 2
        capture_checkpoint("03_after_game_setup_before_record", output_dir)

        print("Starting OBS recording through SprunkiController.start_recording()...")
        if not obs.start_recording():
            print("RESULT: FAIL - OBS start_recording failed.")
            return 2
        capture_checkpoint("04_after_start_recording", output_dir)

        print(f"Recording for {args.seconds:.1f}s with game window visible...")
        time.sleep(args.seconds)
        capture_checkpoint("05_before_stop_recording", output_dir)

        stop_success, output_path = obs.stop_recording()
        if not stop_success or not output_path:
            print(f"RESULT: FAIL - stop_recording failed or no output path: {output_path}")
            return 2

        video_analysis = analyze_video(Path(output_path), output_dir)
        (output_dir / "video_analysis.json").write_text(json.dumps(video_analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Workflow recorded file analysis: {json.dumps(video_analysis, ensure_ascii=False)}")
        if video_analysis["looksBlack"]:
            print("RESULT: FAIL - creator workflow recording output is black.")
            return 2
        print("RESULT: PASS - creator workflow recording output has visible pixels.")
        return 0
    except BaseException as exc:
        import traceback
        print("RESULT: FAIL - unhandled exception in creator workflow validation.")
        print(f"Exception: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 99
    finally:
        if args.cleanup:
            try:
                player.close_game_window()
            except Exception as exc:
                print(f"Cleanup warning: close_game_window failed: {exc}")
            try:
                obs.comprehensive_obs_cleanup()
            except Exception as exc:
                print(f"Cleanup warning: comprehensive_obs_cleanup failed: {exc}")
        else:
            print("Cleanup skipped. Use --cleanup to enable final browser/OBS cleanup.")
        try:
            log_handle.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

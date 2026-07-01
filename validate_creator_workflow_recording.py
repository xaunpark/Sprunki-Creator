import argparse
import json
import os
import time
from pathlib import Path

from sprunki_game_player import SprunkiGamePlayer
from sprunki_obs_controller import SprunkiController
from validate_obs_recording import analyze_video
from validate_obs_capture import connect_obs, get_screenshot, load_config, obs_request


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
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = write_temp_config(args.url, output_dir)
    (output_dir / "workflow_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    obs = SprunkiController()
    player = SprunkiGamePlayer()
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
    finally:
        try:
            player.close_game_window()
        except Exception:
            pass
        try:
            obs.comprehensive_obs_cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

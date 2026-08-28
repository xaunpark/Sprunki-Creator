"""Shared browser-native mouse gesture executor.

The caller owns operation budgets and verification.  This module only emits
one bounded physical gesture and never retries or falls back to another
gesture after partial input has been sent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


SendCommand = Callable[[Any, dict], Optional[dict]]
SleepFn = Callable[[float], None]


@dataclass
class GestureExecutionResult:
    success: bool
    action_start_time: Optional[float] = None
    drop_time: Optional[float] = None
    error: str = ""


def _send(
    ws: Any,
    send_command: SendCommand,
    method: str,
    params: dict,
) -> bool:
    response = send_command(ws, {"method": method, "params": params})
    return response is not None and "error" not in response


def _sleep(seconds: float, sleep_fn: SleepFn) -> None:
    if seconds > 0:
        sleep_fn(seconds)


def execute_cdp_click(
    ws: Any,
    send_command: SendCommand,
    x: float,
    y: float,
    *,
    hold_ms: int = 50,
    sleep_fn: SleepFn = time.sleep,
) -> GestureExecutionResult:
    """Emit one browser-native click without changing the caller's verifier.

    The click is deliberately a complete mouse gesture on one CDP target.
    ``buttons`` is included on both button transitions so the browser receives
    the same state information as a real primary-button click.  This helper
    never retries or falls back after a partial gesture.
    """
    action_start = time.monotonic()
    mouse_pressed = False
    try:
        x = float(x)
        y = float(y)
        hold_ms = max(0, int(hold_ms))

        if not _send(ws, send_command, "Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": x,
            "y": y,
        }):
            return GestureExecutionResult(False, action_start, error="move_failed")

        if not _send(ws, send_command, "Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 1,
            "clickCount": 1,
        }):
            return GestureExecutionResult(False, action_start, error="press_failed")
        mouse_pressed = True
        _sleep(hold_ms / 1000.0, sleep_fn)

        release_time = time.monotonic()
        if not _send(ws, send_command, "Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 0,
            "clickCount": 1,
        }):
            return GestureExecutionResult(
                False,
                action_start,
                release_time,
                "release_failed",
            )
        mouse_pressed = False
        return GestureExecutionResult(True, action_start, release_time)
    except Exception as exc:
        return GestureExecutionResult(False, action_start, error=str(exc))
    finally:
        if mouse_pressed:
            try:
                _send(ws, send_command, "Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 0,
                    "clickCount": 1,
                })
            except Exception:
                pass


def execute_cdp_gesture(
    ws: Any,
    send_command: SendCommand,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    *,
    mode: str = "drag",
    drag_duration_ms: int = 800,
    click_interval_ms: int = 300,
    first_click_duration_ms: int = 100,
    focus_click_duration_ms: int = 100,
    hold_before_move_ms: int = 200,
    approach_ms: int = 0,
    focus_delay_ms: int = 0,
    focus_click: bool = False,
    drop_wait_ms: int = 0,
    post_drop_ms: int = 0,
    move_steps: Optional[int] = None,
    sleep_fn: SleepFn = time.sleep,
) -> GestureExecutionResult:
    """Emit one click-drag or double-click-drag gesture.

    For ``double_click_drag``, ``click_interval_ms`` is measured from the
    first mouse-down to the second mouse-down. ``drag_duration_ms`` starts at
    the second mouse-down and ends at mouse-up. This matches the recorder's
    timeline semantics. ``focus_click`` is reserved for the gameplay
    click-drag path; a manual compound action never adds that extra click.
    """
    action_start = time.monotonic()
    mouse_pressed = False
    mouse_button = "left"
    try:
        start_x = float(start_x)
        start_y = float(start_y)
        end_x = float(end_x)
        end_y = float(end_y)
        drag_duration_ms = max(0, int(drag_duration_ms))
        click_interval_ms = max(0, int(click_interval_ms))
        first_click_duration_ms = max(0, int(first_click_duration_ms))
        focus_click_duration_ms = max(0, int(focus_click_duration_ms))
        hold_before_move_ms = max(0, int(hold_before_move_ms))

        if not _send(ws, send_command, "Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": start_x,
            "y": start_y,
        }):
            return GestureExecutionResult(False, action_start, error="initial_move_failed")
        _sleep(max(0, approach_ms) / 1000.0, sleep_fn)

        if mode == "double_click_drag":
            first_click_duration_ms = min(first_click_duration_ms, click_interval_ms)
            if not _send(ws, send_command, "Input.dispatchMouseEvent", {
                "type": "mousePressed",
                "x": start_x,
                "y": start_y,
                "button": mouse_button,
                "buttons": 1,
                "clickCount": 1,
            }):
                return GestureExecutionResult(False, action_start, error="first_press_failed")
            mouse_pressed = True
            _sleep(first_click_duration_ms / 1000.0, sleep_fn)
            if not _send(ws, send_command, "Input.dispatchMouseEvent", {
                "type": "mouseReleased",
                "x": start_x,
                "y": start_y,
                "button": mouse_button,
                "buttons": 0,
                "clickCount": 1,
            }):
                return GestureExecutionResult(False, action_start, error="first_release_failed")
            mouse_pressed = False

            _sleep(max(click_interval_ms - first_click_duration_ms, 0) / 1000.0, sleep_fn)
            if not _send(ws, send_command, "Input.dispatchMouseEvent", {
                "type": "mousePressed",
                "x": start_x,
                "y": start_y,
                "button": mouse_button,
                "buttons": 1,
                "clickCount": 2,
            }):
                return GestureExecutionResult(False, action_start, error="second_press_failed")
            mouse_pressed = True
            _sleep(min(hold_before_move_ms, drag_duration_ms) / 1000.0, sleep_fn)
            move_duration_ms = max(drag_duration_ms - hold_before_move_ms, 0)
            release_click_count = 2
        else:
            if focus_click:
                if not _send(ws, send_command, "Input.dispatchMouseEvent", {
                    "type": "mousePressed",
                    "x": start_x,
                    "y": start_y,
                    "button": mouse_button,
                    "buttons": 1,
                    "clickCount": 1,
                }):
                    return GestureExecutionResult(False, action_start, error="focus_press_failed")
                _sleep(focus_click_duration_ms / 1000.0, sleep_fn)
                if not _send(ws, send_command, "Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "x": start_x,
                    "y": start_y,
                    "button": mouse_button,
                    "buttons": 0,
                    "clickCount": 1,
                }):
                    return GestureExecutionResult(False, action_start, error="focus_release_failed")
            _sleep(max(0, focus_delay_ms) / 1000.0, sleep_fn)
            if not _send(ws, send_command, "Input.dispatchMouseEvent", {
                "type": "mousePressed",
                "x": start_x,
                "y": start_y,
                "button": mouse_button,
                "buttons": 1,
                "clickCount": 1,
                }):
                    return GestureExecutionResult(False, action_start, error="press_failed")
            mouse_pressed = True
            _sleep(min(hold_before_move_ms, drag_duration_ms) / 1000.0, sleep_fn)
            move_duration_ms = max(drag_duration_ms - hold_before_move_ms, 0)
            release_click_count = 1

        steps = move_steps if move_steps is not None else max(5, min(40, int(round(move_duration_ms / 1000.0 * 30))))
        steps = max(1, int(steps))
        step_delay = (move_duration_ms / 1000.0) / steps
        for step in range(1, steps + 1):
            progress = step / steps
            current_x = start_x + ((end_x - start_x) * progress)
            current_y = start_y + ((end_y - start_y) * progress)
            if not _send(ws, send_command, "Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": current_x,
                "y": current_y,
                "button": mouse_button,
                "buttons": 1,
            }):
                return GestureExecutionResult(False, action_start, error="drag_move_failed")
            _sleep(step_delay, sleep_fn)

        _sleep(max(0, drop_wait_ms) / 1000.0, sleep_fn)
        drop_time = time.monotonic()
        if not _send(ws, send_command, "Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": end_x,
            "y": end_y,
            "button": mouse_button,
            "buttons": 0,
            "clickCount": release_click_count,
        }):
            return GestureExecutionResult(False, action_start, drop_time, "release_failed")
        mouse_pressed = False
        _sleep(max(0, post_drop_ms) / 1000.0, sleep_fn)
        return GestureExecutionResult(True, action_start, drop_time)
    except Exception as exc:
        return GestureExecutionResult(False, action_start, error=str(exc))
    finally:
        if mouse_pressed:
            try:
                _send(ws, send_command, "Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "x": end_x,
                    "y": end_y,
                    "button": mouse_button,
                    "buttons": 0,
                    "clickCount": 1,
                })
            except Exception:
                pass

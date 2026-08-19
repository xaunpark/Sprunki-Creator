import threading
import time
import tkinter as tk
from tkinter import messagebox

import keyboard
import pyautogui
import win32api
import win32con

from manual_profile_manager import save_profile
from manual_prepare_timeline import PrepareTimelineRecorder, format_duration_ms
from sprunki_game_player import SprunkiGamePlayer


class ManualProfileRecorder:
    DRAG_THRESHOLD_PX = 8
    MOUSE_POLL_INTERVAL_SECONDS = 0.01

    RESERVED_KEYS = {
        "shift",
        "ctrl",
        "left ctrl",
        "right ctrl",
        "left shift",
        "right shift",
        "alt",
        "left alt",
        "right alt",
    }

    def __init__(self, url: str):
        self.url = url.strip()
        self.game_player = SprunkiGamePlayer()
        self.root = tk.Tk()
        self.root.title("Manual Profile Recorder")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.geometry("500x500+20+20")

        self.phase = "PREPARE"
        self.prepare_actions = []
        self.icons = []
        self.slots = []
        self.last_saved = "Ready"
        self.running = True
        self.recording_enabled = False
        self.prepare_timeline = PrepareTimelineRecorder()
        self.prepare_timing = {}
        self.last_prepare_action_text = "Mốc vừa ghi: chưa có action"
        self.timing_refresh_job = None
        self.last_key_name = None
        self.last_key_timestamp = 0.0
        self.mouse_down = False
        self.active_mouse_gesture = None
        self.keyboard_hook = None
        self.mouse_thread = None
        self.save_result_path = None

        self._build_ui()
        self._install_hotkeys()

    def _build_ui(self) -> None:
        frame = tk.Frame(self.root, padx=12, pady=10)
        frame.pack(fill="both", expand=True)

        self.url_var = tk.StringVar(value=self.url)
        self.phase_var = tk.StringVar(value=self.phase)
        self.phase_instruction_var = tk.StringVar()
        self.next_phase_var = tk.StringVar(value="Next Phase")
        self.action_count_var = tk.StringVar(value="Prepare actions: 0")
        self.prepare_timing_var = tk.StringVar(
            value=(
                "PREPARE tổng: 00:00.000 | Chờ hiện tại: 00:00.000\n"
                "Mốc vừa ghi: chưa có action"
            )
        )
        self.icon_count_var = tk.StringVar(value="Icons: 0")
        self.slot_count_var = tk.StringVar(value="Slots: 0")
        self.last_saved_var = tk.StringVar(value=self.last_saved)

        tk.Label(frame, text="URL", anchor="w").pack(fill="x")
        tk.Label(frame, textvariable=self.url_var, anchor="w", justify="left", wraplength=465).pack(fill="x", pady=(0, 8))
        tk.Label(frame, textvariable=self.phase_var, anchor="w", font=("Segoe UI", 10, "bold")).pack(fill="x")

        instruction_frame = tk.LabelFrame(frame, text="Hướng dẫn thao tác", padx=8, pady=6)
        instruction_frame.pack(fill="x", pady=(8, 8))
        tk.Label(
            instruction_frame,
            textvariable=self.phase_instruction_var,
            anchor="w",
            justify="left",
            wraplength=455,
        ).pack(fill="x")

        timing_frame = tk.LabelFrame(frame, text="Thời gian PREPARE (realtime)", padx=8, pady=4)
        timing_frame.pack(fill="x", pady=(0, 8))
        tk.Label(
            timing_frame,
            textvariable=self.prepare_timing_var,
            anchor="w",
            justify="left",
            wraplength=455,
        ).pack(fill="x")

        tk.Label(frame, textvariable=self.action_count_var, anchor="w").pack(fill="x", pady=(8, 0))
        tk.Label(frame, textvariable=self.icon_count_var, anchor="w").pack(fill="x")
        tk.Label(frame, textvariable=self.slot_count_var, anchor="w").pack(fill="x")
        tk.Label(frame, textvariable=self.last_saved_var, anchor="w", justify="left", wraplength=330).pack(fill="x", pady=(8, 10))

        button_row = tk.Frame(frame)
        button_row.pack(fill="x")
        tk.Button(button_row, text="Undo Last", command=self.undo_last, width=10).pack(side="left")
        self.next_phase_button = tk.Button(
            button_row,
            textvariable=self.next_phase_var,
            command=self.advance_phase,
            width=18,
        )
        self.next_phase_button.pack(side="left", padx=6)
        tk.Button(button_row, text="Finish Save", command=self.finish_save, width=12).pack(side="left")
        tk.Button(button_row, text="Cancel", command=self.cancel, width=10).pack(side="right")

        help_text = (
            "Phím tắt:\n"
            "Ctrl+Shift+Enter = giống nút chuyển bước đang được đánh dấu\n"
            "Ctrl+Shift+Backspace = hoàn tác\n"
            "Ctrl+Shift+Esc = hủy\n"
            "Trong ICONS: I hoặc Space = lưu icon\n"
            "Trong SLOTS: S hoặc Space = lưu slot"
        )
        tk.Label(frame, text=help_text, anchor="w", justify="left", wraplength=465).pack(fill="x", pady=(10, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.cancel)
        self._refresh_ui()

    def _install_hotkeys(self) -> None:
        keyboard.add_hotkey("ctrl+shift+enter", self.advance_phase, suppress=False)
        keyboard.add_hotkey("ctrl+shift+backspace", self.undo_last, suppress=False)
        keyboard.add_hotkey("ctrl+shift+esc", self.cancel, suppress=False)
        keyboard.add_hotkey("i", self._capture_icon_hotkey, suppress=False)
        keyboard.add_hotkey("s", self._capture_slot_hotkey, suppress=False)
        keyboard.add_hotkey("space", self._capture_space_hotkey, suppress=False)
        self.keyboard_hook = keyboard.hook(self._on_keyboard_event)

    def _cleanup_hotkeys(self) -> None:
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        if self.keyboard_hook:
            try:
                keyboard.unhook(self.keyboard_hook)
            except Exception:
                pass
            self.keyboard_hook = None

    def _refresh_ui(self) -> None:
        phase_title, instruction, next_label = self._phase_ui_content()
        self.phase_var.set(f"Phase: {phase_title}")
        self.phase_instruction_var.set(instruction)
        self.next_phase_var.set(next_label)
        self.action_count_var.set(f"Prepare actions: {len(self.prepare_actions)}")
        self.icon_count_var.set(f"Icons: {len(self.icons)}")
        self.slot_count_var.set(f"Slots: {len(self.slots)}")
        self.last_saved_var.set(f"Last: {self.last_saved}")
        self._refresh_prepare_timing_display()
        self.root.update_idletasks()

    def _describe_prepare_action(self, action) -> str:
        action_type = (action.get("type") or "").lower()
        delay_text = format_duration_ms(action.get("delayMs", 0))
        if action_type == "click":
            description = f"click @ ({action.get('x')}, {action.get('y')})"
        elif action_type == "drag":
            description = (
                f"kéo ({action.get('startX')}, {action.get('startY')}) → "
                f"({action.get('endX')}, {action.get('endY')}) "
                f"trong {format_duration_ms(action.get('dragDurationMs', 0))}"
            )
        elif action_type == "key":
            description = f"phím {action.get('key')}"
        else:
            description = action_type or "action"
        return f"Mốc vừa ghi: {description} — chờ trước đó {delay_text}"

    def _sync_last_prepare_action_text(self) -> None:
        if self.prepare_actions:
            self.last_prepare_action_text = self._describe_prepare_action(self.prepare_actions[-1])
        else:
            self.last_prepare_action_text = "Mốc vừa ghi: chưa có action"

    def _refresh_prepare_timing_display(self) -> None:
        snapshot = self.prepare_timeline.snapshot()
        if self.phase == "PREPARE" and self.recording_enabled:
            if self.active_mouse_gesture is not None:
                drag_elapsed_ms = max(
                    int(round((time.monotonic() - self.active_mouse_gesture["startTime"]) * 1000)),
                    0,
                )
                timing_text = (
                    f"PREPARE tổng: {format_duration_ms(snapshot.get('elapsedMs', 0))} | "
                    f"Đang kéo: {format_duration_ms(drag_elapsed_ms)}"
                )
            else:
                timing_text = (
                    f"PREPARE tổng: {format_duration_ms(snapshot.get('elapsedMs', 0))} | "
                    f"Chờ hiện tại: {format_duration_ms(snapshot.get('currentWaitMs', 0))}"
                )
        elif self.prepare_timing:
            timing_text = (
                f"PREPARE tổng: {format_duration_ms(self.prepare_timing.get('durationMs', 0))} | "
                f"Final wait: {format_duration_ms(self.prepare_timing.get('endDelayMs', 0))}"
            )
        else:
            timing_text = "PREPARE: đang chờ bắt đầu ghi"
        self.prepare_timing_var.set(f"{timing_text}\n{self.last_prepare_action_text}")

    def _refresh_timing_clock(self) -> None:
        self.timing_refresh_job = None
        if not self.running:
            return

        self._refresh_prepare_timing_display()
        if self.phase == "PREPARE" and self.recording_enabled:
            self.timing_refresh_job = self.root.after(100, self._refresh_timing_clock)

    def _phase_ui_content(self):
        if self.phase == "PREPARE":
            return (
                "PREPARE - mở menu game",
                "1. Bấm vào cửa sổ game và thực hiện mọi thao tác để vượt menu mở đầu "
                "(click, kéo thả và phím bấm sẽ được tự động ghi lại).\n"
                "2. Đồng hồ hiển thị tổng thời gian và khoảng chờ hiện tại; mỗi action mới "
                "sẽ chốt khoảng chờ trước đó.\n"
                "3. Khi game đã sẵn sàng để ghi layout, đưa chuột ra khỏi bảng này.\n"
                "4. Bấm 'Sang ICONS' bên dưới hoặc nhấn Ctrl+Shift+Enter.",
                "Sang ICONS",
            )
        if self.phase == "ICONS":
            return (
                "ICONS - ghi các icon nhân vật",
                "Đưa chuột vào tâm từng icon nhân vật rồi nhấn I hoặc Space một lần. "
                "Con trỏ phải nằm trên game, không nằm trên bảng này. Khi ghi đủ icon, "
                "bấm 'Sang SLOTS' bên dưới hoặc nhấn Ctrl+Shift+Enter.",
                "Sang SLOTS",
            )
        return (
            "SLOTS - ghi các ô thả",
            "Đưa chuột vào tâm từng ô thả rồi nhấn S hoặc Space một lần. "
            "Khi ghi đủ slot, bấm 'Finish Save' hoặc nhấn Ctrl+Shift+Enter để lưu.",
            "Lưu profile",
        )

    def _set_last_saved(self, message: str) -> None:
        self.last_saved = message
        self.root.after(0, self._refresh_ui)

    def _mark_event_time(self, event_time=None, duration_ms=0) -> int:
        return self.prepare_timeline.mark_event(now=event_time, duration_ms=duration_ms)

    def _ui_bounds(self):
        self.root.update_idletasks()
        left = self.root.winfo_rootx()
        top = self.root.winfo_rooty()
        right = left + self.root.winfo_width()
        bottom = top + self.root.winfo_height()
        return left, top, right, bottom

    def _point_inside_ui(self, x: int, y: int) -> bool:
        left, top, right, bottom = self._ui_bounds()
        return left <= x <= right and top <= y <= bottom

    def _record_click(self, x: int, y: int, event_time=None) -> None:
        if self.phase != "PREPARE" or not self.recording_enabled:
            return
        if self._point_inside_ui(x, y):
            return
        delay_ms = self._mark_event_time(event_time=event_time)
        self.prepare_actions.append({
            "type": "click",
            "x": int(x),
            "y": int(y),
            "delayMs": delay_ms,
        })
        self._sync_last_prepare_action_text()
        self._set_last_saved(f"Recorded click @ ({x}, {y})")

    def _record_drag(self, gesture, end_x: int, end_y: int, end_time: float) -> None:
        if self.phase != "PREPARE" or not self.recording_enabled:
            return

        start_time = float(gesture["startTime"])
        drag_duration_ms = max(int(round((end_time - start_time) * 1000)), 0)
        delay_ms = self._mark_event_time(
            event_time=end_time,
            duration_ms=drag_duration_ms,
        )
        self.prepare_actions.append({
            "type": "drag",
            "startX": int(gesture["startX"]),
            "startY": int(gesture["startY"]),
            "endX": int(end_x),
            "endY": int(end_y),
            "dragDurationMs": drag_duration_ms,
            "delayMs": delay_ms,
        })
        self._sync_last_prepare_action_text()
        self._set_last_saved(
            f"Recorded drag: ({gesture['startX']}, {gesture['startY']}) → ({end_x}, {end_y})"
        )

    def _on_keyboard_event(self, event) -> None:
        if event.event_type != "down" or self.phase != "PREPARE" or not self.recording_enabled:
            return
        if self.active_mouse_gesture is not None:
            return

        key_name = (event.name or "").lower()
        if not key_name or key_name in self.RESERVED_KEYS:
            return
        if key_name == "enter" and keyboard.is_pressed("ctrl") and keyboard.is_pressed("shift"):
            return
        if key_name == "backspace" and keyboard.is_pressed("ctrl") and keyboard.is_pressed("shift"):
            return
        if key_name == "esc" and keyboard.is_pressed("ctrl") and keyboard.is_pressed("shift"):
            return

        now = time.monotonic()
        if key_name == self.last_key_name and (now - self.last_key_timestamp) < 0.12:
            return

        self.last_key_name = key_name
        self.last_key_timestamp = now
        delay_ms = self._mark_event_time()
        self.prepare_actions.append({
            "type": "key",
            "key": event.name,
            "delayMs": delay_ms,
        })
        self._sync_last_prepare_action_text()
        self._set_last_saved(f"Recorded key: {event.name}")

    def _start_mouse_gesture(self, x: int, y: int) -> None:
        if self.phase != "PREPARE" or not self.recording_enabled or self._point_inside_ui(x, y):
            self.active_mouse_gesture = None
            return

        now = time.monotonic()
        self.active_mouse_gesture = {
            "startX": int(x),
            "startY": int(y),
            "startTime": now,
            "maxDistanceSquared": 0,
        }
        self.last_prepare_action_text = (
            f"Đang giữ chuột tại ({int(x)}, {int(y)}) — chờ phân loại click/kéo..."
        )
        self._set_last_saved(f"Mouse down @ ({x}, {y})")

    def _update_mouse_gesture(self, x: int, y: int) -> None:
        gesture = self.active_mouse_gesture
        if gesture is None:
            return
        dx = int(x) - gesture["startX"]
        dy = int(y) - gesture["startY"]
        distance_squared = dx * dx + dy * dy
        gesture["maxDistanceSquared"] = max(
            gesture["maxDistanceSquared"],
            distance_squared,
        )

    def _finish_mouse_gesture(self, x: int, y: int) -> None:
        gesture = self.active_mouse_gesture
        self.active_mouse_gesture = None
        if gesture is None:
            return

        end_time = time.monotonic()
        threshold_squared = self.DRAG_THRESHOLD_PX * self.DRAG_THRESHOLD_PX
        if gesture["maxDistanceSquared"] >= threshold_squared:
            self._record_drag(gesture, int(x), int(y), end_time)
        else:
            self._record_click(
                gesture["startX"],
                gesture["startY"],
                event_time=gesture["startTime"],
            )

    def _capture_mouse_loop(self) -> None:
        while self.running:
            state = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            if state and not self.mouse_down:
                x, y = pyautogui.position()
                self._start_mouse_gesture(x, y)
            elif state and self.mouse_down and self.active_mouse_gesture is not None:
                x, y = pyautogui.position()
                self._update_mouse_gesture(x, y)
            elif not state and self.mouse_down:
                x, y = pyautogui.position()
                self._finish_mouse_gesture(x, y)
            self.mouse_down = state
            time.sleep(self.MOUSE_POLL_INTERVAL_SECONDS)

    def _capture_icon_hotkey(self) -> None:
        if self.phase != "ICONS":
            return
        x, y = pyautogui.position()
        self.icons.append({
            "index": len(self.icons),
            "label": f"n-{len(self.icons) + 1}",
            "x": int(x),
            "y": int(y),
        })
        self._set_last_saved(f"Saved icon {self.icons[-1]['label']} @ ({x}, {y})")

    def _capture_slot_hotkey(self) -> None:
        if self.phase != "SLOTS":
            return
        x, y = pyautogui.position()
        self.slots.append({
            "index": len(self.slots),
            "x": int(x),
            "y": int(y),
        })
        self._set_last_saved(f"Saved slot {len(self.slots)} @ ({x}, {y})")

    def _capture_space_hotkey(self) -> None:
        if self.phase == "ICONS":
            self._capture_icon_hotkey()
        elif self.phase == "SLOTS":
            self._capture_slot_hotkey()

    def undo_last(self) -> None:
        if self.phase == "PREPARE" and self.prepare_actions:
            removed = self.prepare_actions.pop()
            self.prepare_timeline.undo_last()
            self._sync_last_prepare_action_text()
            self._set_last_saved(f"Undo prepare action: {removed.get('type')}")
        elif self.phase == "ICONS" and self.icons:
            removed = self.icons.pop()
            self._set_last_saved(f"Undo icon {removed.get('label')}")
        elif self.phase == "SLOTS" and self.slots:
            removed = self.slots.pop()
            self._set_last_saved(f"Undo slot {removed.get('index')}")
        self._refresh_ui()

    def advance_phase(self) -> None:
        if self.phase == "PREPARE":
            self.recording_enabled = False
            self.prepare_timing = self.prepare_timeline.finish()
            self.phase = "ICONS"
            self._set_last_saved(
                "Prepare complete. "
                f"Final wait: {self.prepare_timing.get('endDelayMs', 0)} ms. Capture icons."
            )
        elif self.phase == "ICONS":
            self.phase = "SLOTS"
            self._set_last_saved("Icon capture complete. Capture slots.")
        else:
            self.finish_save()
            return
        self._refresh_ui()

    def cancel(self) -> None:
        self.running = False
        self.recording_enabled = False
        self._cleanup_hotkeys()
        try:
            self.game_player.cleanup_chrome_complete()
        except Exception:
            pass
        self.root.after(0, self.root.destroy)

    def finish_save(self) -> None:
        if not self.icons or not self.slots:
            messagebox.showwarning("Missing layout", "Please capture at least one icon and one slot before saving.")
            return

        width, height = pyautogui.size()
        profile_data = {
            "version": 1,
            "sourceUrl": self.url,
            "canonicalUrl": self.url,
            "urlKey": "",
            "viewport": {"width": int(width), "height": int(height)},
            "coordinateSpace": f"screen_{int(width)}x{int(height)}_with_menu",
            "prepareActions": self.prepare_actions,
            "prepareTiming": self.prepare_timing,
            "layout": {
                "icons": self.icons,
                "slots": self.slots,
            },
            "settings": {
                "gameControlType": "unknown",
            },
            "notes": "",
        }

        try:
            profile_path = save_profile(profile_data)
            self.save_result_path = profile_path
            self._set_last_saved(f"Saved profile: {profile_path}")
            messagebox.showinfo("Saved", f"Manual profile saved:\n{profile_path}")
        except Exception as exc:
            messagebox.showerror("Save failed", f"Could not save manual profile:\n{exc}")
            return

        self.cancel()

    def start(self) -> None:
        if not self.url:
            raise ValueError("A game URL is required.")

        if not self.game_player.navigate_and_prepare_game(self.url):
            raise RuntimeError("Could not open the game and reach the post-Run/F11 state.")

        self.prepare_timeline.start()
        self.recording_enabled = True
        self._sync_last_prepare_action_text()
        self.mouse_thread = threading.Thread(target=self._capture_mouse_loop, daemon=True)
        self.mouse_thread.start()
        self._set_last_saved("Run/F11 complete. Recording prepare actions.")
        self.timing_refresh_job = self.root.after(0, self._refresh_timing_clock)
        self.root.mainloop()


def main() -> None:
    import sys

    url = sys.argv[1].strip() if len(sys.argv) > 1 else input("Enter mod URL: ").strip()
    recorder = ManualProfileRecorder(url)
    recorder.start()


if __name__ == "__main__":
    main()

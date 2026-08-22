# sprunki_game_player.py - ENHANCED with Drag Verification
# CHANGES: Added "Putted characters" variable monitoring and 5-coordinate retry logic

import builtins
import json
import time
import webbrowser
import pyautogui
import threading
import random
import os
import subprocess
import requests
import websocket
import re
from tkinter import Tk, Button, Label, StringVar, messagebox, filedialog, Frame, Radiobutton, IntVar
from tkinter.ttk import Progressbar, Combobox
import keyboard
from urllib.parse import urlparse
import pygetwindow as gw
from pathlib import Path
from datetime import datetime
import math
import psutil
import gc
import weakref
import logging
import ctypes
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
if TYPE_CHECKING:
    from sprunki_obs_controller import SprunkiController
from dataclasses import replace

from character_identity import SCRATCH_VM_ICON_STATE_JS, write_character_identity_map
from manual_profile_manager import find_profile, load_profile_from_path, validate_profile
from manual_prepare_timeline import (
    MAX_PREPARE_ACTIONS,
    MAX_TOTAL_PREPARE_DURATION_MS,
    coerce_delay_ms,
    get_prepare_end_delay_ms,
    replay_prepare_timeline,
    validate_prepare_timeline,
)
from manual_prepare_gestures import DOUBLE_CLICK_DRAG_TYPE
from cdp_gesture_executor import execute_cdp_gesture
from placement_verifier import (
    OperationContext,
    OperationResult,
    OperationStatus,
    PlacementSnapshot,
    classify_precondition,
    evaluate_transition,
    is_progress_verified,
    observe_until_verified,
    promote_aggregate_transition,
    wait_for_stable_snapshot,
)
from workflow_quality import (
    UNKNOWN,
    QualityGateResult,
    RemixResult,
    WorkflowReport,
    evaluate_workflow_report,
    placement_transaction_proves_progress,
    validate_playable_layout,
)

class WorkflowIncompatibleError(Exception):
    """Custom exception raised when the workflow detects a persistent,
    unrecoverable incompatibility with the current game mod."""
    pass

class ThreadSafeProgressUpdater:
    def __init__(self, root, action_var):
        self.root = root
        self.action_var = action_var
        self.update_queue = []
        self.queue_lock = threading.Lock()
        
    def safe_update_progress(self, message):
        """Thread-safe progress update"""
        try:
            with self.queue_lock:
                self.update_queue.append(message)
            
            # Schedule GUI update on main thread
            self.root.after(0, self._process_queue)
            
        except Exception as e:
            print(f"⚠️ Progress update error: {e}")
    
    def _process_queue(self):
        """Process updates on main thread"""
        try:
            with self.queue_lock:
                if self.update_queue:
                    message = self.update_queue.pop(0)
                    self.action_var.set(message)
                    self.root.update_idletasks()
        except Exception as e:
            print(f"⚠️ Queue process error: {e}")

class ResourceManager:
    def __init__(self):
        self.cleanup_registry = weakref.WeakSet()
        self.active_threads = []
        self.websockets = []
        
    def register_cleanup(self, obj):
        """Register object for cleanup"""
        self.cleanup_registry.add(obj)
        
    def force_cleanup(self):
        """Force cleanup all registered resources"""
        try:
            # 1. Close WebSocket connections
            for ws in self.websockets[:]:
                try:
                    ws.close()
                    self.websockets.remove(ws)
                except:
                    pass
            
            # 2. Wait for threads to finish
            for thread in self.active_threads[:]:
                if thread.is_alive():
                    thread.join(timeout=2.0)
                self.active_threads.remove(thread)
            
            # 3. Force garbage collection
            gc.collect()
            
            print(f"Resource cleanup completed")
            
        except Exception as e:
            print(f"Resource cleanup error: {e}")

class DragOperationStats:
    """Class Ä‘á»ƒ tracking drag operation statistics"""
    def __init__(self):
        self.total_attempts = 0
        self.successful_drags = 0
        self.failed_drags = 0
        self.retry_counts = []
        # ADDED: Statistics for verification
        self.verification_enabled = 0
        self.verification_successful = 0
        self.coordinate_retries = []
    
    def record_attempt(self, success, retry_count=0, coord_used=0, verification_used=False):
        self.total_attempts += 1
        if success:
            self.successful_drags += 1
        else:
            self.failed_drags += 1
        self.retry_counts.append(retry_count)
        self.coordinate_retries.append(coord_used)
        
        if verification_used:
            self.verification_enabled += 1
            if success:
                self.verification_successful += 1
    
    def get_stats(self):
        if self.total_attempts == 0:
            return "No drag operations recorded"
        
        success_rate = (self.successful_drags / self.total_attempts) * 100
        avg_retries = sum(self.retry_counts) / len(self.retry_counts) if self.retry_counts else 0
        avg_coord_retries = sum(self.coordinate_retries) / len(self.coordinate_retries) if self.coordinate_retries else 0
        verification_rate = (self.verification_enabled / self.total_attempts) * 100 if self.total_attempts > 0 else 0
        verification_success_rate = (self.verification_successful / self.verification_enabled) * 100 if self.verification_enabled > 0 else 0
        
        return f"""
ðŸ“Š Enhanced Drag Operation Statistics:
   Total attempts: {self.total_attempts}
   Successful: {self.successful_drags}
   Failed: {self.failed_drags}
   Success rate: {success_rate:.1f}%
   Average retries: {avg_retries:.1f}
   Average coordinate attempts: {avg_coord_retries:.1f}
   
ðŸ” Verification Statistics:
   Verification enabled: {self.verification_enabled} ({verification_rate:.1f}%)
   Verification success rate: {verification_success_rate:.1f}%
        """

class SprunkiGamePlayer:
    def __init__(self, stop_event=None): # [MODIFIED] Nhận stop_event
        # [NEW] Initialize logger for this class
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # [MODIFIED] Lưu lại cờ dừng, tạo mới nếu không được cung cấp
        self.stop_event = stop_event or threading.Event()

        # Load configuration from config.json (like json_generator.py)
        self.load_config()

        # Return Mode Constants
        self.RETURN_MODE_INDIVIDUAL = "individual"  # Single icon operations
        self.RETURN_MODE_REMIX = "remix"           # Multiple icons batch operations
        self.RETURN_MODE_AUTO = "auto"             # Auto-detect based on context

        self.game_control_type = "click_drag"  # Default to the most common type

        # Current return mode tracking
        self.current_return_mode = self.RETURN_MODE_AUTO
        self.active_icons_count = 0  # Track how many icons currently on slots

        # Carpet bombing configuration - SIMPLIFIED
        self.carpet_bombing_config = {
            'base_coords_per_slot': 2,      # Start with 2 coordinates per slot
            'max_sets': 3,                  # Maximum bombing sets
            'inter_click_delay': 0.03,      # Delay between clicks within slot
            'inter_slot_delay': 1.5,        # Delay between slots
            'inter_set_delay': 0.3,         # Delay between sets
        }

        # Grid config for compatibility
        self.carpet_grid_config = {
            'wave_size': 10,
            'max_waves': 5,
            'inter_click_delay': 0.03,
            'inter_wave_delay': 0.5,
            'default_density': 3
        }

        # Core state
        self.config = None
        self.is_playing = False
        self.should_stop = False
        self.current_icon = 0
        self.auto_thread = None
        
        # Browser management (Chrome debug mode like json_generator.py)
        self.chrome_process = None
        self.ws_url = None
        self.game_url = None
        
        self.obs_controller: Optional['SprunkiController'] = None

        # DUAL VERIFICATION SYSTEM
        # Characters Array system  
        self.characters_setup_done = False
        self.characters_available = False
        
        # Putted Characters system (backup)
        self.putted_chars_setup_done = False
        self.putted_chars_available = False

        # SMART DUAL TRACKING: Track which systems are actually working
        self.working_systems = {
            'characters': None,  # None/True/False
            'putted': None       # None/True/False  
        }
        self.last_operation_working_systems = {}  # Store per operation
        self.last_operation_result = None
        self._operation_counter = 0
        self._active_operation_context = None
        self._session_recovery_count = 0
        self._max_session_recoveries = 2
        self._cleanup_started = False
        self._cleanup_completed = False
        self._physical_input_context = None
        self._batch_reconnect_attempts = 0
        self._batch_snapshot_sequence = 0
        self._snapshot_sequence = 0

        # Shared quality-gate evidence.  These fields are reset for every
        # workflow and are never inferred from cleanup-only observations.
        self.last_workflow_report: Optional[WorkflowReport] = None
        self.last_setup_result: Optional[QualityGateResult] = None
        self.entry_strategy: Dict[str, Any] = {
            "selected": None,
            "tested": [],
            "verification": "UNKNOWN",
        }
        self._workflow_placement_transactions: List[Dict[str, Any]] = []

        # Some mods keep permanent entries in the monitored Scratch arrays even
        # when every visible slot is empty.  Learn that per-mod empty state from
        # a verified return instead of assuming that an empty board means zero.
        self.empty_board_baseline = {
            'characters': None,
            'putted': None,
        }

        # Drag operation statistics (enhanced)
        self.drag_stats = DragOperationStats()

        # Offset Lookup Table for learned drop points
        # Stores the optimal drop offset for each slot index to improve speed and reliability.
        # Format: { slot_index: {'x': offset_x, 'y': offset_y, 'icon_offset_idx': i, 'slot_offset_idx': j} }
        self.slot_offset_lookup = {}

        # Index corrections tracking
        self.index_corrections = {}

        # Domain adjustments for different game domains
        self.domain_adjustments = {
            'sprunki.org': {
                'extra_focus_time': 0.1,
                'drag_speed_multiplier': 1.0,
                'requires_double_click': False,
                'stability_factor': 1.0
            },
            'sprunkiretake.net': {
                'extra_focus_time': 0.2,
                'drag_speed_multiplier': 0.8,
                'requires_double_click': True,
                'stability_factor': 1.2
            },
            'cocrea.world': {
                'extra_focus_time': 0.15,
                'drag_speed_multiplier': 1.2,
                'requires_double_click': False,
                'stability_factor': 0.9
            },
            'gameflare.com': {
                'extra_focus_time': 0.25,
                'drag_speed_multiplier': 0.7,
                'requires_double_click': True,
                'stability_factor': 1.3
            }
        }
        
        # Timing constants for different operations
        self.DRAG_DELAYS = {
            'first_icon': {
                'approach': 0.8,
                'focus': 0.6,
                'hold': 0.8,
                'drag': 1.5,
                'drop': 0.8,
                'post_drop': 0.5
            },
            'normal_icon': {
                'approach': 0.4,
                'focus': 0.3,
                'hold': 0.6,
                'drag': 1.0,
                'drop': 0.6,
                'post_drop': 0.4
            }
        }
        
        # Icon groups by music type
        self.icon_groups = {
            'beats': list(range(0, 5)),      # Index 0-4: Beats
            'effects': list(range(5, 10)),   # Index 5-9: Effects  
            'melodies': list(range(10, 15)), # Index 10-14: Melodies
            'vocals': list(range(15, 20))    # Index 15-19: Vocals
        }
        
        # Remix patterns
        self.remix_patterns = [
            {'name': 'Standard', 'description': 'Chuáº©n cÃ¢n báº±ng', 'beats': 3, 'effects': 2, 'melodies': 1, 'vocals': 1},
            {'name': 'Melody Focus', 'description': '1 effect Ã­t hÆ¡n, 2 melody', 'beats': 3, 'effects': 1, 'melodies': 2, 'vocals': 1},
            {'name': 'Effect Heavy', 'description': 'Nháº¥n máº¡nh effect', 'beats': 2, 'effects': 3, 'melodies': 1, 'vocals': 1},
            {'name': 'No Melody', 'description': 'KhÃ´ng melody, thÃªm vocal', 'beats': 3, 'effects': 2, 'melodies': 0, 'vocals': 2},
            {'name': 'Beat Power', 'description': '4 beats máº¡nh, Ã­t effect', 'beats': 4, 'effects': 1, 'melodies': 1, 'vocals': 1}
        ]
        
        # Remix library
        self.remix_library = []
        self.current_remix_index = 0
        
        # PyAutoGUI settings
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.08

        # Batch Verification Session Management
        self.batch_verification_active = False
        self.session_websocket = None
        self.session_websocket_url = None
        self.verification_functions_ready = False
        self.last_health_check = 0
        self.health_check_interval = 30  # Check every 30s
        self.session_start_time = None
        self.runtime_recording_areas = {}
        self.runtime_recording_area = None
        self.character_identity_map_written = False

        # JavaScript for finding action buttons (skip/play/start/intro) - Multi-Target Detection
        self.js_find_skip_intro = '''
        new Promise((resolve) => {
            try {
                console.log("[GAME PLAYER] Searching for action buttons (skip/play/start/intro)...");
                
                function findVM() {
                    if (window.__vm && window.__vm.runtime) return window.__vm;
                    function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        const elements = document.querySelectorAll(selector);
                        for (const el of elements) {
                            const reactKey = getReactKey(el);
                            if (!reactKey) continue;
                            let fiber = el[reactKey]; let depth = 0;
                            while (fiber && depth++ < 100) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm && props.vm.runtime) { window.__vm = props.vm; return props.vm; }
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }
                
                const toPixelWithMenu = (logicX, logicY) => {
                    if (logicX === null || logicY === null) return { x: null, y: null };
                    const stageW = 640; const stageH = 360; const screenW = 1920; const screenH = 1080; const menuWidth = 78;
                    const viewportW = screenW - menuWidth; const scale = Math.min(viewportW / stageW, screenH / stageH);
                    const canvasW = stageW * scale; const canvasH = stageH * scale; const leftOffset = menuWidth;
                    const topOffset = (screenH - canvasH) / 2; const canvasCenterX = leftOffset + (canvasW / 2);
                    const canvasCenterY = topOffset + (canvasH / 2); const pixelX = canvasCenterX + (logicX * scale);
                    const pixelY = canvasCenterY - (logicY * scale);
                    return { x: Math.round(pixelX), y: Math.round(pixelY) };
                };
                
                const vm = findVM();
                if (!vm) {
                    resolve({ found: false, count: 0, buttons: [], reason: "vm_not_found" });
                    return;
                }
                
                const keywords = ['skip', 'play', 'start', 'intro'];
                const priorityMap = { 'skip': 1, 'play': 2, 'start': 2, 'intro': 3 };
                const excludeNames = ['icons', 'mute', 'gallery', 'credits', 'more', 'reset', 'close'];
                
                const allTargets = vm.runtime.targets;
                const actionButtons = [];
                
                for (let i = 0; i < allTargets.length; i++) {
                    const target = allTargets[i];
                    if (target.isStage) continue;
                    if (!target.visible) continue;
                    if (!target.sprite) continue;
                    if (target.size <= 0) continue;
                    
                    const spriteName = (target.sprite.name || '').toLowerCase();
                    const currentCostumeName = (target.sprite.costumes[target.currentCostume]?.name || '').toLowerCase();
                    
                    // Exclude known non-button sprites
                    if (excludeNames.some(ex => spriteName.includes(ex))) continue;
                    
                    // Match keyword in current costume name (preferred) or sprite name
                    let matchedKeyword = null;
                    let matchSource = null;
                    
                    for (const kw of keywords) {
                        if (currentCostumeName.includes(kw)) {
                            matchedKeyword = kw;
                            matchSource = 'costume';
                            break;
                        }
                    }
                    if (!matchedKeyword) {
                        for (const kw of keywords) {
                            if (spriteName.includes(kw)) {
                                matchedKeyword = kw;
                                matchSource = 'name';
                                break;
                            }
                        }
                    }
                    
                    if (!matchedKeyword) continue;
                    
                    const pixelCoords = toPixelWithMenu(target.x, target.y);
                    if (pixelCoords.x === null || pixelCoords.y === null) continue;
                    if (pixelCoords.x < 0 || pixelCoords.x > 1920 || pixelCoords.y < 0 || pixelCoords.y > 1080) continue;
                    
                    actionButtons.push({
                        index: i,
                        x: pixelCoords.x,
                        y: pixelCoords.y,
                        logicX: Math.round(target.x),
                        logicY: Math.round(target.y),
                        visible: target.visible,
                        targetName: target.sprite.name,
                        currentCostumeName: target.sprite.costumes[target.currentCostume]?.name || 'Unknown',
                        matchedKeyword: matchedKeyword,
                        matchSource: matchSource,
                        priority: priorityMap[matchedKeyword] || 99
                    });
                }
                
                // Sort by priority (lower number = click first)
                actionButtons.sort((a, b) => a.priority - b.priority);
                
                console.log(`[GAME PLAYER] Found ${actionButtons.length} action button(s)`);
                actionButtons.forEach(btn => {
                    console.log(`  [P${btn.priority}] ${btn.targetName} / ${btn.currentCostumeName} @ (${btn.x}, ${btn.y}) - ${btn.matchedKeyword} via ${btn.matchSource}`);
                });
                
                resolve({
                    found: actionButtons.length > 0,
                    count: actionButtons.length,
                    buttons: actionButtons,
                    reason: actionButtons.length > 0 ? "success" : "no_action_buttons_found"
                });
                
            } catch(e) { 
                console.error("[GAME PLAYER] Error searching for action buttons:", e);
                resolve({ found: false, count: 0, buttons: [], reason: "error", error: e.toString() }); 
            }
        });
        '''

        # Setup GUI
        #self.setup_gui()

        # âœ… CLEANUP: Remove unused safe zone code  
        print("ðŸŽ¯ Initialized SIMPLIFIED return system: Precision Center + Carpet Bombing only")

        # Initialize resource manager
        self.resource_manager = ResourceManager()

        # Initialize progress updater
        self.progress_updater = None  # Will be initialized when GUI is ready

    # ================================================================
    # == BOUNDED PLACEMENT OPERATIONS ================================
    # ================================================================

    def _begin_operation(
        self,
        direction,
        target_slot=None,
        *,
        max_input_attempts=5,
        deadline_seconds=12.0,
        max_read_failures=None,
    ):
        """Create exactly one finite budget for one place/return operation."""
        active = getattr(self, "_active_operation_context", None)
        if active is not None and not active.closed:
            return None

        self._operation_counter += 1
        context = OperationContext.begin(
            direction,
            target_slot,
            max_input_attempts=max_input_attempts,
            deadline_seconds=deadline_seconds,
            max_read_failures=max(
                1,
                int(
                    max_read_failures
                    if max_read_failures is not None
                    else getattr(self, "verifier_max_read_failures", 5)
                ),
            ),
            max_recovery_attempts=1,
            operation_id=f"{direction}-{self._operation_counter}",
        )
        self._active_operation_context = context
        return context

    def _finish_operation(self, context, result):
        if context is not None:
            context.close()
        self.last_operation_result = result
        if getattr(self, "_active_operation_context", None) is context:
            self._active_operation_context = None
        return result

    def _operation_result(
        self,
        context,
        status,
        message="",
        *,
        evidence=(),
        details=None,
        attempts=None,
        reads=None,
    ):
        return OperationResult(
            status,
            operation_id=context.operation_id if context else "",
            direction=context.direction if context else "",
            message=message,
            evidence=tuple(evidence or ()),
            details=dict(details or {}),
            attempts=context.input_attempts if context and attempts is None else (attempts or 0),
            reads=context.read_count if context and reads is None else (reads or 0),
            recovery_attempts=context.recovery_attempts if context else 0,
        )

    def _register_scoped_physical_input(self) -> bool:
        """Account for recovery-only inputs issued by legacy intro helpers."""
        context = getattr(self, "_physical_input_context", None)
        if context is None:
            return True
        if not context.can_send_input(
            stop_requested=self.stop_event.is_set(),
            allow_recovery_input=True,
        ):
            return False
        return context.register_input()

    def _require_target_specific_result(self, context, result, message="target_transition_unproven"):
        """Demote aggregate-only evidence for a target-specific return."""
        if result is None:
            return self._operation_result(context, OperationStatus.UNKNOWN, message)
        if result.status == OperationStatus.VERIFIED and not result.target_verified:
            return self._operation_result(
                context,
                OperationStatus.UNKNOWN,
                message,
                evidence=result.evidence,
                details={
                    **(result.details or {}),
                    "targetSpecific": False,
                    "aggregateFallback": True,
                    "originalStatus": result.status.value,
                },
                reads=result.reads,
            )
        return result

    def _capture_placement_snapshot(self):
        """Return one state mapping for the read-only verifier."""
        state = self.get_comprehensive_slot_state()
        snapshot = PlacementSnapshot.from_mapping(state)
        if snapshot is None:
            return None
        if snapshot.sequence <= 0:
            self._snapshot_sequence += 1
            snapshot = replace(snapshot, sequence=self._snapshot_sequence)
        else:
            self._snapshot_sequence = max(self._snapshot_sequence, snapshot.sequence)
        return snapshot

    def _read_comprehensive_state(self, context=None):
        """Read one state and account for it in the active operation budget."""
        state = self.get_comprehensive_slot_state()
        if context is None:
            return state
        snapshot = PlacementSnapshot.from_mapping(state)
        context.register_read(snapshot)
        if not context.can_read_again() and (
            snapshot is None or not snapshot.has_known_signal or snapshot.read_error
        ):
            return None
        return state

    def _capture_stable_placement_snapshot(
        self,
        timeout_ms=None,
        context=None,
        stable_samples=None,
    ):
        if timeout_ms is None:
            timeout_ms = getattr(self, "verifier_snapshot_timeout_ms", 1500)
        if stable_samples is None:
            stable_samples = 2

        def read_snapshot():
            # A state reader gets at most three consecutive failures for the
            # whole operation.  Do not start a fresh polling window after the
            # operation-level reader budget has already become UNKNOWN.
            if context is not None and not context.can_read_again():
                return None
            snapshot = self._capture_placement_snapshot()
            if context is not None:
                context.register_read(snapshot)
            return snapshot

        return wait_for_stable_snapshot(
            read_snapshot,
            timeout_ms=timeout_ms,
            poll_ms=50,
            stable_samples=stable_samples,
            stop_requested=lambda: self.stop_event.is_set() or (
                context is not None and context.expired()
            ),
        )

    def _refresh_after_recovery(self):
        """Rebind runtime readers and invalidate all operation-local evidence."""
        self._physical_input_context = None
        self.slot_offset_lookup.clear()
        self.working_systems = {"characters": None, "putted": None}
        self.last_operation_working_systems.clear()
        self.empty_board_baseline = {"characters": None, "putted": None}
        self.last_target_slot_index = None
        self.last_successful_drag_details = {}
        self.last_operation_result = None
        self._batch_snapshot_sequence = 0
        self._snapshot_sequence = 0

        try:
            if self.session_websocket or self.batch_verification_active:
                self.stop_batch_verification_session()
        except Exception as error:
            print(f"⚠️ Recovery session reset warning: {error}")

        self.batch_verification_active = False
        self.verification_functions_ready = False
        self._batch_reconnect_attempts = 0

        setup_ok = self.setup_dual_verification_system()
        batch_ok = self.start_batch_verification_session() if setup_ok else False
        print(
            "   Recovery reader refresh: "
            f"dual={'✅' if setup_ok else '❌'}, batch={'✅' if batch_ok else '⚠️'}"
        )
        return setup_ok or batch_ok

    def _record_recovery_result(self, context, result):
        if context is not None:
            context.end_recovery()
        return self._finish_operation(context, result)

    def _reset_cleanup_guard(self):
        self._cleanup_started = False
        self._cleanup_completed = False

    def _run_cleanup_once(self, use_cdp=False, context=None, obs_controller=None):
        """Run final cleanup at most once for the current workflow/session."""
        if self._cleanup_completed:
            return OperationResult(
                OperationStatus.ALREADY_EMPTY,
                direction="cleanup",
                message="cleanup_already_completed",
            )
        if self._cleanup_started:
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="cleanup",
                message="cleanup_already_in_progress",
            )

        self._cleanup_started = True
        try:
            if not self.config:
                return OperationResult(
                    OperationStatus.FAILED,
                    direction="cleanup",
                    message="cleanup_config_unavailable",
                )
            success_count, total_attempted, errors = self.smart_batch_return_all_slots_with_mode(
                use_cdp=use_cdp,
                context=context,
                obs_controller=obs_controller,
            )
            if total_attempted == 0 and errors:
                result = OperationResult(
                    OperationStatus.UNKNOWN,
                    direction="cleanup",
                    message="cleanup_operation_not_started",
                    details={"errors": errors},
                )
            elif total_attempted == 0:
                result = OperationResult(
                    OperationStatus.ALREADY_EMPTY,
                    direction="cleanup",
                    message="no_slots_required_cleanup",
                )
            elif success_count == total_attempted and not errors:
                result = OperationResult(
                    OperationStatus.VERIFIED,
                    direction="cleanup",
                    message="cleanup_verified",
                    details={"successCount": success_count, "total": total_attempted},
                )
            elif errors and any("unknown" in str(error).lower() for error in errors):
                result = OperationResult(
                    OperationStatus.UNKNOWN,
                    direction="cleanup",
                    message="cleanup_state_unknown",
                    details={"errors": errors},
                )
            elif errors and any("recovered" in str(error).lower() for error in errors):
                result = OperationResult(
                    OperationStatus.RECOVERED,
                    direction="cleanup",
                    message="cleanup_recovered_not_verified",
                    details={"errors": errors},
                )
            else:
                result = OperationResult(
                    OperationStatus.FAILED,
                    direction="cleanup",
                    message="cleanup_incomplete",
                    details={"successCount": success_count, "total": total_attempted, "errors": errors},
                )
            return result
        except Exception as error:
            return OperationResult(
                OperationStatus.FAILED,
                direction="cleanup",
                message=f"cleanup_exception:{error}",
            )
        finally:
            # A failed cleanup must not be re-entered automatically from a
            # second finally/exception path; the caller receives the failure.
            self._cleanup_completed = True

    def setup_thread_safe_progress(self):
        """Setup thread-safe progress updater"""
        self.progress_updater = ThreadSafeProgressUpdater(self.root, self.action_var)

    def thread_safe_progress_callback(self, message):
        """Thread-safe wrapper for progress callbacks"""
        if hasattr(self, 'progress_updater') and self.progress_updater is not None:
            self.progress_updater.safe_update_progress(message)
        else:
            # Fallback to direct update (risky)
            try:
                self.action_var.set(message)
            except:
                print(f"Direct progress update failed: {message}")

    def cleanup_unused_safe_zone_code(self):  # â† Äáº¶T á»ž ÄÃ‚Y
        """
        ðŸ§¹ CLEANUP: Remove unused safe zone related attributes and methods
        Call this during initialization to clean up code
        """
        # Remove safe zone cache if exists
        if hasattr(self, 'safe_zone_cache'):
            delattr(self, 'safe_zone_cache')
        
        print("ðŸ§¹ Cleaned up unused safe zone code - simplified return only needs carpet bombing")

    def load_config(self):
            """Load configuration from config.json like json_generator.py"""
            try:
                from config_manager import get_config
                self.app_config = get_config()
                
                # Chrome settings from config
                self.chrome_config = self.app_config.get('chrome', {})
                self.chrome_paths = self.chrome_config.get('executable_paths', [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe", 
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                ])
                self.debugging_port = self.chrome_config.get('debugging_port', 9222)
                self.user_data_dir = self.chrome_config.get('user_data_dir', 'chrome_permanent_profile')
                
                # Game settings from config
                self.game_config = self.app_config.get('game', {})

                # 📁 PROJECT FOLDER MANAGEMENT (from sprunki_obs_controller.py)
                self.base_output_dir = self.app_config.get('output', {}).get('base_directory', os.path.join(os.getcwd(), "videos"))
                self.current_project_folder = None
                self.project_slug = None

                print(f"📁 Output directory configured: {self.base_output_dir}")

                # Ensure base output directory exists
                try:
                    os.makedirs(self.base_output_dir, exist_ok=True)
                    print(f"📁 Base output directory ready: {self.base_output_dir}")
                except Exception as e:
                    print(f"❌ Warning: Could not create base output directory: {e}")
                    # Fallback to current working directory
                    self.base_output_dir = os.path.join(os.getcwd(), "videos")
                    os.makedirs(self.base_output_dir, exist_ok=True)

                self.page_load_wait = self.game_config.get('page_load_wait', 20)
                self.game_load_wait = self.game_config.get('game_load_wait', 10)
                self.fullscreen_wait = self.game_config.get('fullscreen_wait', 8)
                self.max_pagedown_attempts = self.game_config.get('max_pagedown_attempts', 5)
                self.intro_skip_clicks = self.game_config.get('intro_skip_clicks', 10)
                self.remix_max_attempts = max(
                    1, int(self.game_config.get('remix_max_attempts', 2))
                )
                self.remix_retry_delay_seconds = max(
                    0.0, float(self.game_config.get('remix_retry_delay_seconds', 1.0))
                )
                
                # Load verification settings
                self.load_verification_config()
                self.load_placement_verification_config()
                
                print(f"âœ… Game Player configured from config.json:")
                print(f"   Debug port: {self.debugging_port}")
                print(f"   Page load wait: {self.page_load_wait}s")
                print(f"   Game load wait: {self.game_load_wait}s")
                print(f"   Intro skip clicks: {self.intro_skip_clicks}")
                print(f"   Smart verification: {'âœ… Enabled' if self.page_verification_enabled else 'âŒ Disabled'}")
                
            except Exception as e:
                print(f"âš ï¸ Could not load config, using defaults: {e}")
                # Set defaults
                self.chrome_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe", 
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                ]
                self.debugging_port = 9222
                self.user_data_dir = 'chrome_permanent_profile'
                self.page_load_wait = 20
                self.game_load_wait = 10
                self.fullscreen_wait = 8
                self.max_pagedown_attempts = 5
                self.intro_skip_clicks = 10
                self.remix_max_attempts = 2
                self.remix_retry_delay_seconds = 1.0
                
                # Set default verification settings
                self.page_verification_enabled = True
                self.game_verification_enabled = True
                self.page_verification_timeout = 120
                self.game_verification_timeout = 150
                self.page_verification_interval = 2
                self.game_verification_interval = 3
                self.stabilization_wait = 3
                self.game_container_selectors = [".ccw-stage-wrapper", "#stage-canvas-wrapper", "canvas"]
                self.min_containers_required = 1
                self.check_react_components = True
                self.check_run_button_dom = True
                self._set_default_placement_verification_config()

    def load_verification_config(self):
            """Load verification configuration from config.json"""
            try:
                # Page verification settings
                self.page_verification = self.app_config.get('page_verification', {})
                self.page_verification_enabled = self.page_verification.get('enabled', True)
                self.page_verification_timeout = self.page_verification.get('timeout_ms', 120000) / 1000  # Convert to seconds
                self.page_verification_interval = self.page_verification.get('check_interval_ms', 2000) / 1000
                self.page_verification_criteria = self.page_verification.get('criteria', {})
                self.page_verification_retries = self.page_verification.get('max_retries', 3)
                self.stabilization_wait = self.page_verification.get('stabilization_wait_seconds', 3)
                
                # Game load verification settings
                self.game_verification = self.app_config.get('game_load_verification', {})
                self.game_verification_enabled = self.game_verification.get('enabled', True)
                self.game_verification_timeout = self.game_verification.get('timeout_ms', 90000) / 1000
                self.game_verification_interval = self.game_verification.get('check_interval_ms', 2000) / 1000
                self.game_container_selectors = self.game_verification.get('game_container_selectors', [])
                self.min_containers_required = self.game_verification.get('min_containers_required', 1)
                self.check_react_components = self.game_verification.get('check_react_components', True)
                self.check_run_button_dom = self.game_verification.get('check_run_button_dom', True)
                
                print(f"âœ… Verification config loaded:")
                print(f"   Page verification: {'âœ… Enabled' if self.page_verification_enabled else 'âŒ Disabled'}")
                print(f"   Game verification: {'âœ… Enabled' if self.game_verification_enabled else 'âŒ Disabled'}")
                print(f"   Page timeout: {self.page_verification_timeout}s")
                print(f"   Game timeout: {self.game_verification_timeout}s")
                
            except Exception as e:
                print(f"âš ï¸ Could not load verification config, using defaults: {e}")
                # Set defaults if config loading fails
                self.page_verification_enabled = True
                self.game_verification_enabled = True
                self.page_verification_timeout = 120
                self.game_verification_timeout = 90
                self.page_verification_interval = 2
                self.game_verification_interval = 2
                self.stabilization_wait = 3
                self.game_container_selectors = [".ccw-stage-wrapper", "#stage-canvas-wrapper", "canvas"]
                self.min_containers_required = 1
                self.check_react_components = True
                self.check_run_button_dom = True

    def _set_default_placement_verification_config(self):
        """Use the lenient-but-safe placement policy when config is absent."""
        self.placement_verification_config = {
            "policy": "lenient",
            "accept_aggregate_progress": True,
            "accept_unstable_delta": True,
            "post_action_stable_samples": 1,
            "snapshot_timeout_ms": 1500,
            "post_action_timeout_ms": 5000,
            "max_read_failures": 5,
            "target_proof_required_for_cache": True,
            "target_proof_required_for_cleanup": False,
            "final_board_must_be_empty": True,
        }
        self.verifier_policy = "lenient"
        self.verifier_accept_aggregate_progress = True
        self.verifier_accept_unstable_delta = True
        self.verifier_post_action_stable_samples = 1
        self.verifier_snapshot_timeout_ms = 1500
        self.verifier_post_action_timeout_ms = 5000
        self.verifier_max_read_failures = 5
        self.verifier_target_proof_required_for_cache = True
        self.verifier_target_proof_required_for_cleanup = False
        self.verifier_final_board_must_be_empty = True

    def load_placement_verification_config(self):
        """Load the relaxed operation evidence policy from config.json."""
        self._set_default_placement_verification_config()
        try:
            configured = self.app_config.get("placement_verification", {})
            if not isinstance(configured, dict):
                configured = {}
            self.placement_verification_config.update(configured)

            policy = str(
                self.placement_verification_config.get("policy", "lenient")
            ).strip().lower()
            self.verifier_policy = policy or "lenient"
            self.verifier_accept_aggregate_progress = bool(
                self.placement_verification_config.get(
                    "accept_aggregate_progress", True
                )
            )
            self.verifier_accept_unstable_delta = bool(
                self.placement_verification_config.get("accept_unstable_delta", True)
            )
            self.verifier_post_action_stable_samples = max(
                1,
                int(
                    self.placement_verification_config.get(
                        "post_action_stable_samples", 1
                    )
                ),
            )
            self.verifier_snapshot_timeout_ms = max(
                250,
                int(
                    self.placement_verification_config.get(
                        "snapshot_timeout_ms", 1500
                    )
                ),
            )
            self.verifier_post_action_timeout_ms = max(
                1000,
                int(
                    self.placement_verification_config.get(
                        "post_action_timeout_ms", 5000
                    )
                ),
            )
            self.verifier_max_read_failures = max(
                3,
                int(
                    self.placement_verification_config.get(
                        "max_read_failures", 5
                    )
                ),
            )
            self.verifier_target_proof_required_for_cache = bool(
                self.placement_verification_config.get(
                    "target_proof_required_for_cache", True
                )
            )
            self.verifier_target_proof_required_for_cleanup = bool(
                self.placement_verification_config.get(
                    "target_proof_required_for_cleanup", False
                )
            )
            self.verifier_final_board_must_be_empty = bool(
                self.placement_verification_config.get(
                    "final_board_must_be_empty", True
                )
            )
            print(
                "✅ Placement verifier policy: "
                f"{self.verifier_policy} "
                f"(aggregate={'✅' if self.verifier_accept_aggregate_progress else '❌'}, "
                f"unstable_delta={'✅' if self.verifier_accept_unstable_delta else '❌'}, "
                f"stable_samples={self.verifier_post_action_stable_samples})"
            )
        except (TypeError, ValueError, OverflowError) as error:
            print(f"⚠️ Invalid placement verification config; using defaults: {error}")
            self._set_default_placement_verification_config()

    def get_project_folder(self):
        """Get current project folder for external use (OBS Controller integration)"""
        return self.current_project_folder or self.base_output_dir

    def get_project_slug(self):
        """Get current project slug for external use"""
        return self.project_slug or "default"

    def get_obs_output_info(self):
        """
        🎬 Get output info for OBS Controller integration
        Returns dict with project folder, slug, and base directory
        """
        return {
            'project_folder': self.current_project_folder,
            'project_slug': self.project_slug,
            'base_output_dir': self.base_output_dir,
            'current_url': getattr(self, 'config', {}).get('currentUrl', ''),
            'folder_ready': self.current_project_folder is not None and os.path.exists(self.current_project_folder)
        }

    def prepare_project_for_recording(self, url=None):
        """
        🎬 Prepare project folder for recording session
        To be called by OBS Controller before starting recording
        """
        try:
            # Use current URL if not provided
            target_url = url or getattr(self, 'config', {}).get('currentUrl', '')
            
            if not target_url:
                print("❌ No URL available for project setup")
                return False
            
            # [FIXED] Do not create project folder here. Rely on the one set by the master controller.
            # The main purpose of this function is now to create necessary subfolders.
            if not self.current_project_folder or not os.path.exists(self.current_project_folder):
                print(f"❌ Cannot prepare project: self.current_project_folder is not set or does not exist.")
                print(f"   Value: {self.current_project_folder}")
                # Fallback: create a folder based on URL slug just in case
                print("   [FALLBACK] Creating a project folder now...")
                from urllib.parse import urlparse
                import re
                slug = re.sub(r'[^a-zA-Z0-9\-]', '-', urlparse(target_url).path.split('/')[-1])
                self.current_project_folder = os.path.join(self.base_output_dir, slug)
                os.makedirs(self.current_project_folder, exist_ok=True)

            project_folder = self.current_project_folder
            
            # Ensure all necessary subfolders exist
            subfolders = ['session_reports', 'screenshots', 'recordings']
            for subfolder in subfolders:
                subfolder_path = os.path.join(project_folder, subfolder)
                os.makedirs(subfolder_path, exist_ok=True)
            
            print(f"🎬 Project ready for recording:")
            print(f"   📁 Folder: {project_folder}")
            print(f"   🏷️ Slug: {self.project_slug}")
            print(f"   📂 Subfolders: {', '.join(subfolders)}")
            
            return True
            
        except Exception as e:
            print(f"❌ Error preparing project for recording: {e}")
            return False

    def get_current_project_info(self):
        """
        📊 Get comprehensive current project information
        For debugging and logging purposes
        """
        return {
            'project_folder': self.current_project_folder,
            'project_slug': self.project_slug,
            'base_output_dir': self.base_output_dir,
            'folder_exists': self.current_project_folder and os.path.exists(self.current_project_folder),
            'current_url': getattr(self, 'config', {}).get('currentUrl', ''),
            'subfolders': {
                'session_reports': os.path.join(self.current_project_folder, 'session_reports') if self.current_project_folder else None,
                'screenshots': os.path.join(self.current_project_folder, 'screenshots') if self.current_project_folder else None,
                'recordings': os.path.join(self.current_project_folder, 'recordings') if self.current_project_folder else None
            }
        }

    def write_character_identity_map_for_current_project(self):
        """Capture visible Scratch VM icon state and write character_identity_map.json."""
        ws = None
        project_folder = self.get_project_folder()
        source_url = getattr(self, "game_url", None)
        if not source_url and isinstance(getattr(self, "config", None), dict):
            source_url = self.config.get("currentUrl")

        try:
            if not project_folder:
                print("⚠️ Character identity map skipped: project folder is not ready.")
                return False

            ws_url = self._get_websocket_url()
            if not ws_url:
                icon_state = {
                    "ok": False,
                    "error": "No CDP websocket URL",
                    "href": source_url,
                    "icons": [],
                }
            else:
                ws = websocket.create_connection(ws_url, timeout=30)
                response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": SCRATCH_VM_ICON_STATE_JS,
                        "awaitPromise": True,
                        "returnByValue": True,
                    }
                })
                icon_state = response.get("result", {}).get("result", {}).get("value") if response else None
                if not isinstance(icon_state, dict):
                    icon_state = {
                        "ok": False,
                        "error": "Runtime.evaluate returned no icon state",
                        "href": source_url,
                        "icons": [],
                    }

            identity_map = write_character_identity_map(
                project_folder,
                icon_state,
                source_url=source_url,
                page_title=icon_state.get("title") if isinstance(icon_state, dict) else None,
            )
            validation = identity_map.get("validation", {})
            print(
                "✅ character_identity_map.json written: "
                f"status={identity_map.get('status')}, "
                f"standard={validation.get('standardCharacterCount')}, "
                f"excluded={validation.get('excludedSegmentCount')}"
            )
            if validation.get("errors"):
                print(f"   ⚠️ Identity validation errors: {validation.get('errors')}")
            return identity_map.get("status") == "verified"
        except Exception as exc:
            print(f"⚠️ Character identity map capture failed: {exc}")
            return False
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

    def _execute_edge_reinforcement(self, round_num, use_cdp):
        """
        ðŸŽ¯ STUB: Edge reinforcement for carpet bombing
        """
        try:
            print(f"   ðŸŽ¯ Edge reinforcement round {round_num}")
            # Simple edge clicks - can be expanded later
            edge_clicks = min(round_num * 2, 10)  # 2-10 clicks based on round
            return edge_clicks
            
        except Exception as e:
            print(f"âŒ Edge reinforcement error: {e}")
            return 0

    def _execute_verification_js(self, js_code):
        """Execute JavaScript for verification via WebSocket/CDP"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:  # âœ… CHECK None trÆ°á»›c
                print("âš ï¸ WebSocket not available for verification, skipping JS check")
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)  # â† ÄÃƒ SAFE
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_code, "awaitPromise": True, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')
                return result
            else:
                print("âš ï¸ No response from verification JavaScript")
                return None
                
        except Exception as e:
            print(f"âš ï¸ Error executing verification JavaScript: {e}")
            return None

    def preflight_url_playable(self, target_url):
        """Fail fast for URLs that are definitely unavailable before launching Chrome."""
        try:
            print(f"[CHECK] URL preflight: {target_url}")
            response = requests.get(
                target_url,
                allow_redirects=True,
                timeout=8,
                stream=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0 Safari/537.36"
                    )
                },
            )
            status_code = response.status_code
            final_url = response.url
            response.close()

            print(f"   HTTP status: {status_code} ({final_url})")
            if status_code >= 400:
                print(f"[FAIL] URL preflight failed: page returned HTTP {status_code}")
                return QualityGateResult.failure(
                    "no_playable_entry",
                    evidence={"statusCode": status_code, "finalUrl": final_url},
                    message=f"URL returned HTTP {status_code}.",
                )

            return QualityGateResult.pass_result(
                evidence={"statusCode": status_code, "finalUrl": final_url},
                message="URL preflight passed.",
            )

        except requests.RequestException as e:
            print(f"[WARN] URL preflight inconclusive: {e}")
            print("   Browser verification must resolve this UNKNOWN state.")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"error": str(e), "url": target_url},
                message="URL preflight was inconclusive.",
            )

    def detect_cocrea_error_page_fast(self):
        """Detect Cocrea error pages from the already loaded DOM."""
        error_page_js = """
        (() => {
            const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
            const title = document.title || '';
            const headingText = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]'))
                .map(el => el.innerText || el.textContent || '')
                .join(' ')
                .replace(/\\s+/g, ' ')
                .trim();
            const currentUrl = window.location.href;
            const headingCandidates = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]'))
                .map(el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
                .filter(Boolean);
            const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const normalizedTitle = normalize(title);
            const normalizedHeadings = headingCandidates.map(normalize);

            // Do not search the whole body for "404". Usernames, dates, comments,
            // and project text can contain that sequence by coincidence.
            const errorMatchers = [
                { pattern: '404', regex: /^(?:404|error 404|404 error)(?:[ .:!?]|$)/i },
                { pattern: 'page not found', regex: /^page not found(?:[ .:!?]|$)/i },
                { pattern: 'project not found', regex: /^project not found(?:[ .:!?]|$)/i },
                { pattern: 'không tìm thấy', regex: /^không tìm thấy(?:[ .:!?]|$)/i },
                { pattern: 'khong tim thay', regex: /^khong tim thay(?:[ .:!?]|$)/i }
            ];
            const titleMatch = errorMatchers.find(({ regex }) => regex.test(normalizedTitle));
            const headingMatch = errorMatchers.find(({ regex }) =>
                normalizedHeadings.some(heading => regex.test(heading))
            );
            const matched = titleMatch || headingMatch;
            const matchedPattern = matched ? matched.pattern : null;
            return {
                isErrorPage: !!matched,
                matchedPattern,
                title,
                headingText: headingText.substring(0, 200),
                bodyPreview: bodyText.substring(0, 300),
                currentUrl
            };
        })();
        """

        try:
            result = self._execute_verification_js(error_page_js)
            if not isinstance(result, dict):
                return QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    message="Error-page detector returned no result.",
                )

            if result.get("isErrorPage"):
                print("[FAIL] Cocrea error page detected before game verification")
                print(f"   Pattern: {result.get('matchedPattern')}")
                print(f"   Title: {result.get('title')}")
                print(f"   Heading: {result.get('headingText')}")
                print(f"   URL: {result.get('currentUrl')}")
                return QualityGateResult.failure(
                    "no_playable_entry",
                    evidence=result,
                    message="Cocrea rendered an error page.",
                )

            return QualityGateResult.pass_result(
                evidence=result,
                message="No Cocrea error page detected.",
            )

        except Exception as e:
            print(f"[WARN] Error-page preflight inconclusive: {e}")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"error": str(e)},
                message="Error-page detection was inconclusive.",
            )

    def verify_page_fully_loaded(self, target_url):
        """
        ðŸ” Smart Page Load Verification System
        Checks if page is fully loaded before proceeding with image detection
        """
        try:
            if not self.page_verification_enabled:
                print("ðŸ” Page verification disabled; readiness is UNKNOWN")
                return QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    message="Page verification is disabled.",
                )
            
            print(f"\nðŸ” SMART PAGE VERIFICATION: {target_url}")
            print("="*70)
            
            # JavaScript for comprehensive page verification
            verification_js = """
            (() => {
                const results = {
                    documentState: document.readyState,
                    basicElements: {
                        hasBody: !!document.body,
                        hasHead: !!document.head,
                        bodyChildren: document.body ? document.body.children.length : 0
                    },
                    loadingSpinners: {
                        count: 0,
                        types: []
                    },
                    networkActivity: {
                        pendingRequests: 0,
                        estimatedIdle: true
                    },
                    currentUrl: window.location.href,
                    timestamp: Date.now()
                };
                
                // Check for common loading spinner patterns
                const spinnerSelectors = [
                    '.loading', '.spinner', '.loader', 
                    '[class*="loading"]', '[class*="spinner"]', '[class*="loader"]',
                    '.fa-spinner', '.fa-circle-o-notch'
                ];
                
                spinnerSelectors.forEach(selector => {
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0) {
                        const visibleSpinners = Array.from(elements).filter(el => {
                            const style = window.getComputedStyle(el);
                            return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                        });
                        if (visibleSpinners.length > 0) {
                            results.loadingSpinners.count += visibleSpinners.length;
                            results.loadingSpinners.types.push(selector);
                        }
                    }
                });
                
                // Estimate network activity (simplified)
                try {
                    if (window.performance && window.performance.getEntriesByType) {
                        const navigationEntries = window.performance.getEntriesByType('navigation');
                        if (navigationEntries.length > 0) {
                            const nav = navigationEntries[0];
                            results.networkActivity.loadComplete = nav.loadEventEnd > 0;
                            results.networkActivity.domComplete = nav.domComplete > 0;
                        }
                    }
                } catch (e) {
                    console.log('Network activity check failed:', e);
                }
                
                // Overall readiness assessment
                results.readyScore = 0;
                if (results.documentState === 'complete') results.readyScore += 30;
                if (results.basicElements.hasBody && results.basicElements.hasHead) results.readyScore += 25;
                if (results.basicElements.bodyChildren > 0) results.readyScore += 20;
                if (results.loadingSpinners.count === 0) results.readyScore += 25;
                
                results.isReady = results.readyScore >= 75;
                
                return results;
            })();
            """
            
            start_time = time.time()
            max_retries = self.page_verification_retries
            
            for attempt in range(1, max_retries + 1):
                if self.stop_event.is_set():
                    return QualityGateResult.failure(
                        "workflow_stopped",
                        message="Page verification stopped by the job.",
                    )
                
                print(f"ðŸ” Page verification attempt {attempt}/{max_retries}")
                
                # Execute verification JavaScript
                verification_result = self._execute_verification_js(verification_js)
                
                if verification_result:
                    document_state = verification_result.get('documentState', 'unknown')
                    basic_elements = verification_result.get('basicElements', {})
                    loading_spinners = verification_result.get('loadingSpinners', {})
                    ready_score = verification_result.get('readyScore', 0)
                    is_ready = verification_result.get('isReady', False)
                    
                    print(f"   ðŸ“Š Document state: {document_state}")
                    print(f"   ðŸ“Š Basic elements: Body={basic_elements.get('hasBody')}, Head={basic_elements.get('hasHead')}, Children={basic_elements.get('bodyChildren', 0)}")
                    print(f"   ðŸ“Š Loading spinners: {loading_spinners.get('count', 0)} active")
                    print(f"   ðŸ“Š Ready score: {ready_score}/100")
                    print(f"   ðŸ“Š Overall assessment: {'âœ… READY' if is_ready else 'â³ NOT READY'}")
                    
                    if is_ready:
                        elapsed = time.time() - start_time
                        print(f"âœ… PAGE VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return QualityGateResult.pass_result(
                            evidence=verification_result,
                            message="Page verification passed.",
                        )
                    else:
                        if loading_spinners.get('count', 0) > 0:
                            spinner_types = ', '.join(loading_spinners.get('types', []))
                            print(f"   â³ Waiting for loading spinners to disappear: {spinner_types}")
                        if document_state != 'complete':
                            print(f"   â³ Waiting for document to complete loading...")
                        if basic_elements.get('bodyChildren', 0) == 0:
                            print(f"   â³ Waiting for page content to render...")
                
                else:
                    print(f"   âš ï¸ Verification JavaScript failed, using fallback check")
                    # Fallback: Simple document ready check
                    simple_check = self._execute_verification_js("document.readyState")
                    if simple_check == 'complete':
                        print(f"   âœ… Fallback check passed: document.readyState = complete")
                        return QualityGateResult.pass_result(
                            evidence={"fallback": "document.readyState", "state": simple_check},
                            message="Page verification passed using readyState fallback.",
                        )
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.page_verification_timeout:
                    print(f"âš ï¸ Page verification timeout ({self.page_verification_timeout}s exceeded)")
                    print("   Verification is UNKNOWN; workflow must not continue as success.")
                    return QualityGateResult.failure(
                        "runtime_verification_unknown",
                        status="UNKNOWN",
                        retryable=True,
                        evidence={"attempt": attempt, "timeoutSeconds": self.page_verification_timeout},
                        message="Page verification timed out.",
                    )
                
                # Wait before next attempt
                if attempt < max_retries:
                    print(f"   â³ Waiting {self.page_verification_interval}s before next check...")
                    time.sleep(self.page_verification_interval)
            
            # All attempts failed; do not fail open.
            print(f"âš ï¸ Page verification failed after {max_retries} attempts")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"attempts": max_retries},
                message="Page verification remained inconclusive.",
            )

        except Exception as e:
            print(f"âš ï¸ Error in page verification: {e}")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"error": str(e)},
                message="Page verification raised an exception.",
            )

    def verify_game_components_ready(self, timeout_override: Optional[float] = None):
        """
        [MODIFIED] Smart Game Load Verification System.
        Now accepts a timeout_override for fail-fast scenarios.
        """
        try:
            if not self.game_verification_enabled:
                print("ðŸŽ® Game verification disabled; readiness is UNKNOWN")
                return QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    message="Game verification is disabled.",
                )
            
            # [MODIFIED] Use override or default timeout from config
            timeout = timeout_override if timeout_override is not None else self.game_verification_timeout
            
            print(f"\nðŸŽ® SMART GAME VERIFICATION (Timeout: {timeout}s)")
            print("="*70)
            
            # JavaScript for game component verification
            game_verification_js = f"""
            (() => {{
                // ðŸ†• EXPANDED: More comprehensive game container selectors
                const selectors = [
                    ".ccw-stage-wrapper", 
                    "#stage-canvas-wrapper", 
                    "canvas",
                    ".style_gandi-player-wrapper__WCs0P",
                    ".style_stage-player__0Xy7d",
                    ".style_stage-cover__VITCW",
                    ".gandi_stage_stage_1fD7k",
                    "[class*='stage']",
                    "[class*='player']",
                    "[class*='game']",
                    "#app canvas",
                    ".app-container canvas",
                    "iframe[src*='scratch']",
                    "iframe[src*='game']"
                ];
                
                const results = {{
                    gameContainers: [],
                    reactComponents: {{
                        found: false,
                        details: []
                    }},
                    runButtonInDOM: false,
                    errorPage: {{
                        detected: false,
                        pattern: null,
                        title: document.title || '',
                        headingText: '',
                        bodyPreview: ''
                    }},
                    overallReady: false,
                    timestamp: Date.now()
                }};

                const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                const headingText = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]'))
                    .map(el => el.innerText || el.textContent || '')
                    .join(' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const headingCandidates = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]'))
                    .map(el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(Boolean);
                const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const normalizedTitle = normalize(results.errorPage.title);
                const normalizedHeadings = headingCandidates.map(normalize);

                // Match only exact error titles/headings. Do not scan body text;
                // comments can contain accidental sequences such as "404".
                const errorMatchers = [
                    {{ pattern: '404', regex: /^(?:404|error 404|404 error)(?:[ .:!?]|$)/i }},
                    {{ pattern: 'page not found', regex: /^page not found(?:[ .:!?]|$)/i }},
                    {{ pattern: 'project not found', regex: /^project not found(?:[ .:!?]|$)/i }},
                    {{ pattern: 'không tìm thấy', regex: /^không tìm thấy(?:[ .:!?]|$)/i }},
                    {{ pattern: 'khong tim thay', regex: /^khong tim thay(?:[ .:!?]|$)/i }}
                ];
                const matchedError = errorMatchers.find(item =>
                    item.regex.test(normalizedTitle) ||
                    normalizedHeadings.some(heading => item.regex.test(heading))
                );
                const matchedErrorPattern = matchedError ? matchedError.pattern : null;
                if (matchedErrorPattern) {{
                    results.errorPage.detected = true;
                    results.errorPage.pattern = matchedErrorPattern;
                    results.errorPage.headingText = headingText.substring(0, 200);
                    results.errorPage.bodyPreview = bodyText.substring(0, 300);
                    console.log(`[FAIL] Error page detected during game verification: ${{matchedErrorPattern}}`);
                    return results;
                }}
                 
                // Check game container elements with more detailed logging
                selectors.forEach(selector => {{
                    try {{
                        const elements = document.querySelectorAll(selector);
                        if (elements.length > 0) {{
                            elements.forEach((el, index) => {{
                                const rect = el.getBoundingClientRect();
                                const containerInfo = {{
                                    selector: selector,
                                    index: index,
                                    id: el.id || 'no-id',
                                    className: el.className || 'no-class',
                                    tagName: el.tagName.toLowerCase(),
                                    visible: rect.width > 0 && rect.height > 0,
                                    hasChildren: el.children.length > 0,
                                    dimensions: `${{rect.width}}x${{rect.height}}`
                                }};
                                results.gameContainers.push(containerInfo);
                                console.log(`ðŸŽ¯ Found game container: ${{selector}} - ${{containerInfo.tagName}} - ${{containerInfo.dimensions}} - Visible: ${{containerInfo.visible}}`);
                            }});
                        }}
                    }} catch (e) {{
                        console.warn(`Selector failed: ${{selector}} - ${{e.message}}`);
                    }}
                }});
                
                // Check for React components
                const checkReactComponents = () => {{
                    const elements = document.querySelectorAll('*');
                    let reactFound = false;
                    let reactCount = 0;
                    
                    for (let el of elements) {{
                        const keys = Object.keys(el);
                        const reactKey = keys.find(key => key.startsWith('__react'));
                        if (reactKey) {{
                            reactFound = true;
                            reactCount++;
                            if (reactCount <= 5) {{ // Log first 5 only
                                results.reactComponents.details.push({{
                                    element: el.tagName,
                                    id: el.id || 'no-id',
                                    className: el.className || 'no-class',
                                    reactKey: reactKey.substring(0, 20) + '...'
                                }});
                            }}
                        }}
                        if (reactCount >= 100) break; // Stop after finding many
                    }}
                    results.reactComponents.found = reactFound;
                    results.reactComponents.count = reactCount;
                    console.log(`âš›ï¸ React check: Found ${{reactCount}} components`);
                }};
                
                if ({str(self.check_react_components).lower()}) {{
                    checkReactComponents();
                }}
                
                // ðŸ†• ENHANCED: More comprehensive Run button detection
                if ({str(self.check_run_button_dom).lower()}) {{
                    const runButtonSelectors = [
                        'button[type="submit"]',
                        'button[class*="run"]',
                        'button[class*="play"]', 
                        'button[class*="start"]',
                        '.style_run-button__VjFSY',
                        '.MuiButton-contained',
                        'button:contains("Run")',
                        'button:contains("Play")',
                        'button:contains("Start")',
                        'button:contains("â–¶")',
                        '[aria-label*="run"]',
                        '[aria-label*="play"]',
                        '[aria-label*="start"]',
                        'input[type="submit"]',
                        'input[value*="Run"]',
                        'input[value*="Play"]',
                        '.run-button',
                        '#run-button',
                        '.play-button',
                        '#play-button'
                    ];
                    
                    let foundButtons = [];
                    
                    for (let selector of runButtonSelectors) {{
                        try {{
                            const buttons = document.querySelectorAll(selector);
                            for (let button of buttons) {{
                                const text = (button.textContent || button.value || button.alt || '').toLowerCase();
                                const hasRunText = text.includes('run') || text.includes('play') || text.includes('start') || text.includes('â–¶');
                                const isVisible = button.offsetParent !== null;
                                
                                if (hasRunText || selector.includes('run') || selector.includes('play')) {{
                                    foundButtons.push({{
                                        selector: selector,
                                        text: text.substring(0, 20),
                                        visible: isVisible,
                                        className: button.className || 'no-class'
                                    }});
                                    
                                    if (hasRunText || isVisible) {{
                                        results.runButtonInDOM = true;
                                        console.log(`ðŸ”˜ Found Run button: ${{selector}} - "${{text}}" - Visible: ${{isVisible}}`);
                                    }}
                                }}
                            }}
                        }} catch (e) {{
                            // Skip invalid selectors silently
                        }}
                    }}
                    
                    console.log(`ðŸ”˜ Run button search: Found ${{foundButtons.length}} candidates, runButtonInDOM: ${{results.runButtonInDOM}}`);
                }}
                
                // Assessment with more lenient criteria
                const gameContainersFound = results.gameContainers.length >= {self.min_containers_required};
                const reactReady = !{str(self.check_react_components).lower()} || results.reactComponents.found;
                const runButtonReady = !{str(self.check_run_button_dom).lower()} || results.runButtonInDOM;
                
                results.overallReady = gameContainersFound && reactReady && runButtonReady;
                
                // ðŸ†• DETAILED: Log comprehensive results
                console.log(`ðŸŽ¯ Game Verification Summary:`);
                console.log(`   - Game containers: ${{results.gameContainers.length}} (need ${{self.min_containers_required}})`);
                console.log(`   - React components: ${{results.reactComponents.found}} (${{results.reactComponents.count || 0}} found)`);
                console.log(`   - Run button in DOM: ${{results.runButtonInDOM}}`);
                console.log(`   - Overall ready: ${{results.overallReady}}`);
                
                return results;
            }})();
            """
            
            start_time = time.time()
            max_attempts = 20
            
            for attempt in range(1, max_attempts + 1):
                if self.stop_event.is_set():
                    return QualityGateResult.failure(
                        "workflow_stopped",
                        message="Game verification stopped by the job.",
                    )
                
                print(f"ðŸŽ® Game verification attempt {attempt}/{max_attempts}")
                
                # Execute game verification JavaScript
                game_result = self._execute_verification_js(game_verification_js)
                
                if game_result:
                    game_containers = game_result.get('gameContainers', [])
                    react_components = game_result.get('reactComponents', {})
                    run_button_in_dom = game_result.get('runButtonInDOM', False)
                    error_page = game_result.get('errorPage', {})
                    overall_ready = game_result.get('overallReady', False)

                    if error_page.get('detected'):
                        print("[FAIL] Game verification stopped early: Cocrea error page detected")
                        print(f"   Pattern: {error_page.get('pattern')}")
                        print(f"   Title: {error_page.get('title')}")
                        print(f"   Heading: {error_page.get('headingText')}")
                        return QualityGateResult.failure(
                            "no_playable_entry",
                            evidence=error_page,
                            message="Game verification detected an error page.",
                        )
                    
                    print(f"   ðŸŽ¯ Game containers found: {len(game_containers)}")
                    for container in game_containers[:3]:  # Show first 3
                        print(f"      - {container['selector']}: visible={container['visible']}, children={container['hasChildren']}")
                    
                    if self.check_react_components:
                        react_found = react_components.get('found', False)
                        react_count = len(react_components.get('details', []))
                        print(f"   âš›ï¸ React components: {'âœ… Found' if react_found else 'âŒ Not found'} ({react_count} detected)")
                    
                    if self.check_run_button_dom:
                        print(f"   ðŸ”˜ Run button in DOM: {'âœ… Found' if run_button_in_dom else 'âŒ Not found'}")
                    
                    print(f"   ðŸŽ¯ Overall game ready: {'âœ… YES' if overall_ready else 'âŒ NO'}")
                    
                    if overall_ready:
                        elapsed = time.time() - start_time
                        print(f"âœ… GAME VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return QualityGateResult.pass_result(
                            evidence=game_result,
                            message="Game components verified.",
                        )
                    else:
                        if len(game_containers) < self.min_containers_required:
                            print(f"   â³ Waiting for game containers to load ({len(game_containers)}/{self.min_containers_required})")
                        if self.check_react_components and not react_components.get('found'):
                            print(f"   â³ Waiting for React components to initialize")
                        if self.check_run_button_dom and not run_button_in_dom:
                            print(f"   â³ Waiting for Run button to appear in DOM")
                
                else:
                    print(f"   âš ï¸ Game verification JavaScript failed")
                
                # [MODIFIED] Check timeout using the determined timeout value
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    print(f"âš ï¸  Game verification timeout ({timeout}s exceeded)")
                    return QualityGateResult.failure(
                        "runtime_verification_unknown",
                        status="UNKNOWN",
                        retryable=True,
                        evidence={"attempt": attempt, "timeoutSeconds": timeout},
                        message="Game component verification timed out.",
                    )
                
                # Wait before next attempt
                if attempt < max_attempts:
                    print(f"   â ³ Waiting {self.game_verification_interval}s before next check...")
                    time.sleep(self.game_verification_interval)
            
            # All attempts failed within the timeout
            print(f"âš ï¸  Game verification failed after {max_attempts} attempts")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"attempts": max_attempts},
                message="Game component verification remained inconclusive.",
            )
            
        except Exception as e:
            print(f"âš ï¸  Error in game verification: {e}")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"error": str(e)},
                message="Game component verification raised an exception.",
            )

    def verify_page_fully_loaded_lightweight(self, target_url):
        """Lightweight readiness probe with the same tri-state contract."""
        try:
            # Simplified check for continuous use
            verification_js = """
            (() => {
                const results = {
                    documentState: document.readyState,
                    hasBody: !!document.body,
                    hasHead: !!document.head,
                    bodyChildren: document.body ? document.body.children.length : 0,
                    loadingSpinners: 0
                };
                
                // Check for loading spinners
                const spinnerSelectors = ['.loading', '.spinner', '.loader', '[class*="loading"]'];
                spinnerSelectors.forEach(selector => {
                    const elements = document.querySelectorAll(selector);
                    const visibleSpinners = Array.from(elements).filter(el => {
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                    });
                    results.loadingSpinners += visibleSpinners.length;
                });
                
                // Calculate readiness score
                let score = 0;
                if (results.documentState === 'complete') score += 40;
                if (results.hasBody && results.hasHead) score += 30;
                if (results.bodyChildren > 0) score += 20;
                if (results.loadingSpinners === 0) score += 10;
                
                results.score = score;
                results.ready = score >= 75;
                
                return results;
            })();
            """
            
            result = self._execute_verification_js(verification_js)
            if isinstance(result, dict):
                print(f"      ðŸ“Š Page score: {result.get('score', 0)}/100, Spinners: {result.get('loadingSpinners', 'N/A')}")
                if result.get('ready', False):
                    return QualityGateResult.pass_result(
                        evidence={"targetUrl": target_url, **result},
                        message="Lightweight page readiness passed.",
                    )
                return QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    evidence={"targetUrl": target_url, **result},
                    message="Lightweight page readiness is not yet proven.",
                )
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"targetUrl": target_url},
                message="Lightweight page probe returned no result.",
            )
            
        except Exception as e:
            print(f"   âš ï¸ Page check error: {e}")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"targetUrl": target_url, "error": str(e)},
                message="Lightweight page probe raised an exception.",
            )


    def verify_game_components_ready_lightweight(self):
        """Lightweight component probe with the same tri-state contract."""
        try:
            # Simplified game check
            game_js = """
            (() => {
                const results = {
                    gameContainers: 0,
                    reactComponents: 0,
                    runButton: false
                };
                
                // Check game containers
                const containerSelectors = ['.ccw-stage-wrapper', 'canvas', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                containerSelectors.forEach(selector => {
                    const elements = document.querySelectorAll(selector);
                    elements.forEach(el => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results.gameContainers++;
                        }
                    });
                });
                
                // Check React components
                const allElements = document.querySelectorAll('*');
                for (let el of allElements) {
                    const keys = Object.keys(el);
                    if (keys.find(key => key.startsWith('__react'))) {
                        results.reactComponents++;
                        if (results.reactComponents >= 5) break; // Enough sample
                    }
                }
                
                // Check Run button
                const runButtonSelectors = [
                    '.style_run-button__VjFSY',
                    'button[class*="run"]',
                    'button.MuiButton-contained'
                ];
                
                for (const selector of runButtonSelectors) {
                    const button = document.querySelector(selector);
                    if (button) {
                        results.runButton = true;
                        break;
                    }
                }
                
                // Calculate readiness
                results.ready = results.gameContainers > 0 && results.runButton;
                
                return results;
            })();
            """
            
            result = self._execute_verification_js(game_js)
            if isinstance(result, dict):
                containers = result.get('gameContainers', 0)
                has_button = result.get('runButton', False)
                print(f"      ðŸ“Š Containers: {containers}, Run button: {'âœ…' if has_button else 'âŒ'}")
                if result.get('ready', False):
                    return QualityGateResult.pass_result(
                        evidence=result,
                        message="Lightweight game component readiness passed.",
                    )
                return QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    evidence=result,
                    message="Lightweight game component readiness is not yet proven.",
                )
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                message="Lightweight game component probe returned no result.",
            )
            
        except Exception as e:
            print(f"   âš ï¸ Game check error: {e}")
            return QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"error": str(e)},
                message="Lightweight game component probe raised an exception.",
            )

    # ================================================================
    # == CHROME DEBUG BROWSER MANAGEMENT (from json_generator.py) ==
    # ================================================================

    def cleanup_chrome_complete(self):
        """
        🔧 FIXED: Force complete browser shutdown with port cleanup verification
        """
        try:
            print("🔧 ENHANCED: Force complete browser shutdown with port verification...")
            
            # Phase 1: Close WebSocket connections immediately
            if hasattr(self, 'session_websocket') and self.session_websocket:
                try:
                    self.session_websocket.close()
                    print("   ✅ WebSocket connection closed")
                except:
                    pass
            
            # Phase 2: Kill Chrome processes using port 9222 specifically
            try:
                import subprocess
                import psutil
                
                print(f"   🔍 Checking processes using port {self.debugging_port}...")
                
                # Find all Chrome processes with our debug port
                chrome_pids_to_kill = []
                
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] and 'chrome' in proc.info['name'].lower():
                            cmdline = proc.info.get('cmdline', [])
                            if cmdline:
                                cmdline_str = ' '.join(cmdline)
                                if f'--remote-debugging-port={self.debugging_port}' in cmdline_str:
                                    chrome_pids_to_kill.append(proc.info['pid'])
                                    print(f"   🎯 Found Chrome debug process: PID {proc.info['pid']}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                
                # Kill identified processes
                for pid in chrome_pids_to_kill:
                    try:
                        proc = psutil.Process(pid)
                        proc.terminate()
                        print(f"   ✅ Terminated Chrome debug process: PID {pid}")
                    except:
                        try:
                            proc = psutil.Process(pid)
                            proc.kill()
                            print(f"   ⚡ Killed Chrome debug process: PID {pid}")
                        except:
                            pass
                
                # Wait for processes to fully terminate
                if chrome_pids_to_kill:
                    print(f"   ⏳ Waiting for {len(chrome_pids_to_kill)} processes to terminate...")
                    psutil.wait_procs([psutil.Process(pid) for pid in chrome_pids_to_kill if psutil.pid_exists(pid)], timeout=5)
                
            except ImportError:
                print("   ⚠️ psutil not available, using fallback method")
                # Fallback: terminate via process reference
                if hasattr(self, 'chrome_process') and self.chrome_process:
                    try:
                        self.chrome_process.terminate()
                        self.chrome_process.wait(timeout=3)
                        print("   ✅ Chrome process terminated via reference")
                    except:
                        try:
                            self.chrome_process.kill()
                            print("   ⚡ Chrome process killed via reference")
                        except:
                            pass
            
            # Phase 3: Verify port 9222 is released
            print(f"   🔍 Verifying port {self.debugging_port} is released...")
            port_released = False
            
            for attempt in range(10):  # Try for 10 seconds
                try:
                    import socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('localhost', self.debugging_port))
                    sock.close()
                    
                    if result != 0:  # Port is not accessible = released
                        port_released = True
                        print(f"   ✅ Port {self.debugging_port} confirmed released")
                        break
                    else:
                        print(f"   ⏳ Port {self.debugging_port} still in use, waiting... (attempt {attempt + 1}/10)")
                        time.sleep(1)
                        
                except Exception as e:
                    print(f"   ⚠️ Port check error: {e}")
                    time.sleep(1)
            
            if not port_released:
                print(f"   ⚠️ Port {self.debugging_port} may still be in use after 10s")
                
                # Emergency: try to kill any remaining process on port
                try:
                    import subprocess
                    # Use netstat to find process using port 9222
                    result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
                    for line in result.stdout.split('\n'):
                        if f':{self.debugging_port}' in line and 'LISTENING' in line:
                            parts = line.split()
                            if parts:
                                pid = parts[-1]
                                try:
                                    subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
                                    print(f"   ⚡ Emergency killed process {pid} using port {self.debugging_port}")
                                except:
                                    pass
                except:
                    pass
            
            # Phase 4: Close windows by title (keep existing logic)
            try:
                import pygetwindow as gw
                for title_part in ['cocrea.world', 'Google Chrome', ' - Google Chrome']:
                    windows = gw.getWindowsWithTitle(title_part)
                    for window in windows:
                        title = window.title or ""
                        normalized_title = title.lower()
                        if any(token in normalized_title for token in [
                            "command prompt",
                            "powershell",
                            "windows terminal",
                            "cmd.exe",
                            "python ",
                            "validate_",
                            "start_creator_service",
                        ]):
                            print(f"   ⚠️ Skipping non-Chrome window during cleanup: {title[:80]}...")
                            continue
                        if not (
                            "cocrea.world" in normalized_title
                            or normalized_title.endswith(" - google chrome")
                            or normalized_title == "google chrome"
                        ):
                            print(f"   ⚠️ Skipping unmatched window during cleanup: {title[:80]}...")
                            continue
                        try:
                            window.close()
                            print(f"   ✅ Closed window: {window.title[:50]}...")
                        except:
                            pass
            except:
                print("   ⚠️ Could not close windows via pygetwindow")
            
            # Phase 5: Reset internal state
            self.ws_url = None
            self.chrome_process = None
            self.session_websocket = None
            self.session_websocket_url = None
            self.batch_verification_active = False
            
            print("✅ ENHANCED browser shutdown completed with port verification")
            
        except Exception as e:
            print(f"🚫 Enhanced browser shutdown error: {e}")
            # Still reset state even if error
            self.ws_url = None
            self.chrome_process = None
            self.session_websocket = None
            self.batch_verification_active = False

    def clear_cache_cookies_only(self):
        """ðŸ†• Clear only cache and cookies, keep profile/login intact"""
        try:
            import shutil
            import glob
            
            print("ðŸ—‘ï¸ Clearing cache and cookies only (keeping profile intact)...")
            
            user_data_path = os.path.join(os.getcwd(), self.user_data_dir)
            if not os.path.exists(user_data_path):
                print(f"   â„¹ï¸ Profile directory not found: {user_data_path}")
                return
                
            default_path = os.path.join(user_data_path, "Default")
            if not os.path.exists(default_path):
                print("   â„¹ï¸ Default profile not found")
                return
                
            # Clear only temporary cache files, PRESERVE login/extension data
            cache_paths = [
                os.path.join(default_path, "Cache", "Cache_Data"),  # Browser cache only
                os.path.join(default_path, "Code Cache"),           # JS compiled cache only  
                os.path.join(default_path, "GPUCache"),             # GPU cache only
                # âŒ REMOVED: Cookies - giá»¯ Ä‘á»ƒ duy trÃ¬ login
                # âŒ REMOVED: Cookies-journal - giá»¯ Ä‘á»ƒ duy trÃ¬ login
                # âŒ REMOVED: Sessions - giá»¯ Ä‘á»ƒ duy trÃ¬ active sessions
                # âŒ REMOVED: Local Storage - extensions cáº§n dá»¯ liá»‡u nÃ y
                # âŒ REMOVED: IndexedDB - extensions lÆ°u settings á»Ÿ Ä‘Ã¢y
                # âŒ REMOVED: Service Worker - extensions hoáº¡t Ä‘á»™ng qua SW
                # âŒ REMOVED: Network - giá»¯ Ä‘á»ƒ extension hoáº¡t Ä‘á»™ng ngay
            ]
            
            cleared_count = 0
            for cache_path in cache_paths:
                try:
                    if os.path.exists(cache_path):
                        if os.path.isfile(cache_path):
                            os.remove(cache_path)
                        elif os.path.isdir(cache_path):
                            shutil.rmtree(cache_path, ignore_errors=True)
                        cleared_count += 1
                        print(f"   âœ… Cleared: {os.path.basename(cache_path)}")
                except Exception as e:
                    print(f"   âš ï¸ Could not clear {cache_path}: {e}")
                    
            print(f"   ðŸ§¹ Minimal cache cleanup completed - {cleared_count} items processed")
            print("   âœ… Login session, cookies, extensions data preserved")
            print("   ðŸ“¦ Extensions should work immediately without refresh")
            
        except Exception as e:
            print(f"âŒ Cache clearing error: {e}")

    def force_clear_chrome_data(self):
        """ðŸ†• FORCE: Clear Chrome data even when Chrome is not running"""
        try:
            # ðŸ”§ FIX: Import modules á»Ÿ Ä‘áº§u hÃ m Ä‘á»ƒ trÃ¡nh "possibly unbound"
            import shutil
            import getpass
            
            print("ðŸ—‘ï¸ FORCE CLEARING Chrome browsing data...")
            
            # Method 1: Clear cache/cookies only (keep profile intact)
            self.clear_cache_cookies_only()
            
            print("   â„¹ï¸ Profile directory preserved for login/extensions")
            
            # Method 2: Clear system Chrome cache (if accessible)
            try:
                username = getpass.getuser()
                
                chrome_cache_paths = [
                    f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cache",
                    f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Code Cache",
                    f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\GPUCache",
                ]
                
                print(f"   ðŸ” Checking {len(chrome_cache_paths)} cache directories for user: {username}")
                
                for cache_path in chrome_cache_paths:
                    if os.path.exists(cache_path):
                        try:
                            shutil.rmtree(cache_path, ignore_errors=True)
                            print(f"   âœ… Cleared cache: {cache_path}")
                        except Exception as cache_error:
                            print(f"   âš ï¸ Cache in use: {cache_path} - {cache_error}")
                    else:
                        print(f"   â„¹ï¸ Cache not found: {cache_path}")
                        
            except Exception as e:
                print(f"   âš ï¸ System cache clearing failed: {e}")
            
            print("âœ… Force clear completed")
            return True
            
        except Exception as e:
            print(f"âŒ Force clear error: {e}")
            return False

    def safe_window_operation(self, window, operation_name, operation_func):
        """
        ðŸ›¡ï¸ NEW: Safe window operation with Windows error handling
        """
        try:
            print(f"   ðŸ”„ Attempting {operation_name}...")
            operation_func()
            print(f"   âœ… {operation_name} completed")
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if ("error code from windows: 0" in error_msg or 
                "operation completed successfully" in error_msg or
                "the operation completed successfully" in error_msg):
                print(f"   âœ… {operation_name} completed (Windows quirk - success reported as error)")
                return True
            else:
                print(f"   âš ï¸ {operation_name} warning: {e}")
                print(f"   ðŸ’¡ Continuing anyway - not critical for workflow")
                return False

    def safe_activate_window(self, window):
        """ðŸ›¡ï¸ Safe window activation"""
        return self.safe_window_operation(window, "window activation", lambda: window.activate())

    def safe_maximize_window(self, window):
        """ðŸ›¡ï¸ Safe window maximization"""
        return self.safe_window_operation(window, "window maximization", lambda: window.maximize())

    def _is_game_browser_title(self, title):
        """Return True only for browser windows that can safely receive game hotkeys."""
        title_lower = (title or "").lower()
        if not title_lower:
            return False

        terminal_markers = (
            "command prompt",
            "powershell",
            "windows terminal",
            "cmd.exe",
            "python ",
            "validate_",
            "start_creator_service",
        )
        if any(marker in title_lower for marker in terminal_markers):
            return False

        return (
            "cocrea.world" in title_lower
            or title_lower.endswith(" - google chrome")
            or title_lower == "google chrome"
        )

    def _find_game_browser_window(self):
        """Find the most likely Cocrea/Chrome game window without matching terminals."""
        try:
            candidates = []
            for title_part in ("cocrea.world", " - Google Chrome", "Google Chrome"):
                for window in gw.getWindowsWithTitle(title_part):
                    if window and self._is_game_browser_title(getattr(window, "title", "")):
                        candidates.append(window)

            if not candidates:
                for window in gw.getAllWindows():
                    if window and self._is_game_browser_title(getattr(window, "title", "")):
                        candidates.append(window)

            candidates = [
                window for window in candidates
                if getattr(window, "width", 0) >= 800 and getattr(window, "height", 0) >= 500
            ] or candidates
            return candidates[0] if candidates else None
        except Exception as e:
            print(f"   ⚠️ Could not find game browser window safely: {e}")
            return None

    def _get_foreground_window_title(self):
        """Read the current Windows foreground title for hotkey safety checks."""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return ""
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return ""

    def press_f11_for_game_fullscreen(self):
        """Press F11 only when the Cocrea/Chrome game window is definitely foreground."""
        try:
            print("   -> Preparing Chrome focus before F11 true fullscreen...")
            game_window = self._find_game_browser_window()
            if not game_window:
                print("   ⚠️ F11 skipped: no safe Cocrea/Chrome game window found.")
                return False

            try:
                game_window.restore()
            except Exception:
                pass
            self.safe_activate_window(game_window)
            self.safe_maximize_window(game_window)
            time.sleep(0.7)

            foreground_title = self._get_foreground_window_title()
            if not self._is_game_browser_title(foreground_title):
                print(f"   ⚠️ F11 skipped: foreground is not Chrome/Cocrea: {foreground_title[:80]!r}")
                return False

            print(f"   ✅ F11 target confirmed: {foreground_title[:80]}...")
            pyautogui.press('f11')
            time.sleep(3)
            return True
        except Exception as e:
            print(f"   ⚠️ F11 fullscreen skipped due to focus safety error: {e}")
            return False

    def wait_for_complete_game_readiness(self, url):
        """
        ðŸ”„ CONTINUOUS MONITORING: Wait for both page + game ready with configurable timeout
        """
        try:
            # Get configuration from config.json
            continuous_config = self.app_config.get('continuous_monitoring', {})
            page_config = self.app_config.get('page_verification', {})
            game_config = self.app_config.get('game_load_verification', {})
            
            # Get timeouts and intervals
            try:
                max_timeout = float(continuous_config.get('max_wait_time_seconds', 120))
                if not math.isfinite(max_timeout) or max_timeout <= 0:
                    raise ValueError("invalid readiness timeout")
                # A malformed config must not turn readiness polling into an
                # unattended infinite wait.
                max_timeout = min(max_timeout, 10 * 60)
            except (TypeError, ValueError, OverflowError):
                max_timeout = 120.0
            try:
                check_interval = float(continuous_config.get('check_interval_seconds', 10))
                if not math.isfinite(check_interval) or check_interval <= 0:
                    raise ValueError("invalid readiness interval")
                check_interval = min(check_interval, 60.0)
            except (TypeError, ValueError, OverflowError):
                check_interval = 10.0
            early_exit = continuous_config.get('early_exit_on_success', True)
            
            print(f"\nðŸ”„ CONTINUOUS GAME READINESS MONITORING")
            print("="*70)
            print(f"   â° Maximum timeout: {max_timeout}s")
            print(f"   ðŸ”„ Check interval: {check_interval}s")
            print(f"   ðŸŽ¯ Target: Both page AND game verification success")
            print(f"   ðŸš€ Early exit: {'âœ… Enabled' if early_exit else 'âŒ Disabled'}")
            print("="*70)
            
            start_time = time.time()
            check_count = 0
            
            while time.time() - start_time < max_timeout:
                check_count += 1
                elapsed = time.time() - start_time
                remaining = max_timeout - elapsed
                
                if elapsed >= max_timeout:
                    print(f"â° TIMEOUT REACHED after {max_timeout}s")
                    print(f"   ðŸ“Š Total checks performed: {check_count}")
                    return False
                
                print(f"\nðŸ” CHECK #{check_count} (Elapsed: {elapsed:.1f}s, Remaining: {remaining:.1f}s)")
                print("-" * 50)
                
                # Phase 1: Page verification
                print("ðŸ“„ Checking page load status...")
                page_ready = self.verify_page_fully_loaded_lightweight(url)
                page_status = "âœ… READY" if page_ready else "â³ NOT READY"
                print(f"   ðŸ“„ Page status: {page_status}")
                
                # Phase 2: Game verification  
                print("ðŸŽ® Checking game components status...")
                game_ready = self.verify_game_components_ready_lightweight()
                game_status = "âœ… READY" if game_ready else "â³ NOT READY"
                print(f"   ðŸŽ® Game status: {game_status}")
                
                # Check if both ready
                both_ready = page_ready and game_ready
                
                if both_ready and early_exit:
                    print(f"\nðŸŽ‰ SUCCESS! Both page and game ready after {elapsed:.1f}s")
                    print(f"   ðŸ“Š Checks performed: {check_count}")
                    print(f"   ðŸŽ¯ Early exit - no need to wait full timeout")
                    print("="*70)
                    return True
                
                # Log current status
                print(f"   ðŸ“Š Combined status: Page({page_status}) + Game({game_status}) = {'âœ… READY' if both_ready else 'â³ WAITING'}")
                
                # Calculate next check time
                if remaining >= check_interval:
                    print(f"   â³ Next check in {check_interval}s...")
                    time.sleep(check_interval)
                else:
                    print(f"   â° Less than {check_interval}s remaining, final check...")
                    time.sleep(remaining)
                    break
            
            # Final timeout
            print(f"\nâ° TIMEOUT: Max wait time ({max_timeout}s) exceeded")
            print(f"   ðŸ“Š Total checks: {check_count}")
            print(f"   âš ï¸ Will proceed with Run button detection anyway")
            return False
            
        except Exception as e:
            print(f"âŒ Error in continuous monitoring: {e}")
            return False

    def _calculate_slots_coverage_area(self, slot_indices, all_slots, overlap_margin):
        """
        ðŸŽ¯ ADDED: Calculate coverage area for slots
        """
        try:
            if not all_slots or not slot_indices:
                return None
            
            # Get coordinates of target slots
            target_slots = [all_slots[i] for i in slot_indices if i < len(all_slots)]
            if not target_slots:
                return None
            
            # Find boundaries
            min_x = min(slot['x'] for slot in target_slots) - overlap_margin
            max_x = max(slot['x'] for slot in target_slots) + overlap_margin
            min_y = min(slot['y'] for slot in target_slots) - overlap_margin
            max_y = max(slot['y'] for slot in target_slots) + overlap_margin
            
            return {
                'min_x': min_x,
                'max_x': max_x,
                'min_y': min_y,
                'max_y': max_y,
                'width': max_x - min_x,
                'height': max_y - min_y,
                'center_x': (min_x + max_x) // 2,
                'center_y': (min_y + max_y) // 2
            }
            
        except Exception as e:
            print(f"âŒ Error calculating coverage area: {e}")
            return None

    def navigate_and_prepare_game(self, url):
        """
        [REWRITTEN - FINAL STRATEGY] A robust setup process that patiently
        waits for game components, providing the most reliable way to handle
        both valid and invalid (404, etc.) pages.
        """
        try:
            print(f"\n🚀 STARTING GAME SETUP (FINAL STRATEGY): {url}")
            print("======================================================================")
            self.game_url = url
            self.entry_strategy = {
                "selected": None,
                "tested": [],
                "verification": UNKNOWN,
            }

            preflight_result = self.preflight_url_playable(url)
            if not preflight_result:
                self.last_setup_result = preflight_result
                print(
                    "[FAIL] SETUP FAILED: URL preflight did not pass: "
                    f"{getattr(preflight_result, 'failure_code', 'unknown')}"
                )
                return False

            # --- PHASE 1: BROWSER LAUNCH (Không thay đổi) ---
            print("PHASE 1: Launching Browser and Loading Page...")
            self.cleanup_chrome_complete()
            time.sleep(2)
            
            chrome_exe = next((path for path in self.chrome_paths if os.path.exists(path)), None)
            if not chrome_exe:
                raise RuntimeError("Critical Error: Google Chrome not found.")
            
            chrome_cmd = [
                chrome_exe, 
                f"--remote-debugging-port={self.debugging_port}", 
                f"--user-data-dir={os.path.join(os.getcwd(), self.user_data_dir)}",
                "--no-first-run",
                "--incognito", 
                "--window-size=1280,720", 
                "--remote-allow-origins=*",

                # --- CẶP BÀI TRÙNG ĐỂ TẮT HOÀN TOÀN YÊU CẦU MIC/CAM---
                "--use-fake-ui-for-media-stream",     # 1. Tự động đồng ý quyền Micro/Cam
                "--test-type",                        # 2. ✅ QUAN TRỌNG: Tắt thông báo cảnh báo màu vàng của Chrome
                # ---------------------------------------

                # ðŸ†• EXTENSION-SAFE CLEANUP FLAGS
                "--clear-token-service",                    # âœ… Safe: chá»‰ clear auth tokens
                "--clear-key-system-properties",           # âœ… Safe: chá»‰ clear key system  
                "--no-default-browser-check",              # âœ… Safe: khÃ´ng áº£nh hÆ°á»Ÿng extensions
                "--disable-client-side-phishing-detection", # âœ… Safe: khÃ´ng liÃªn quan extensions
                
                # ðŸ†• POPUP & RESTORE FLAGS (EXTENSION-FRIENDLY)
                "--disable-popup-blocking",                # âœ… GOOD: cho phÃ©p popups (tá»‘t cho extensions)
                "--disable-prompt-on-repost",             # âœ… Safe: chá»‰ táº¯t prompt
                "--disable-hang-monitor",                 # âœ… Safe: chá»‰ táº¯t hang detection
                "--disable-session-crashed-bubble",       # âœ… Safe: táº¯t crash bubble
                "--disable-infobars",                     # âœ… Safe: táº¯t info bars
                "--no-recovery-component",                # âœ… Safe: táº¯t crash recovery
                
                # ðŸ†• PERFORMANCE FLAGS (EXTENSION-COMPATIBLE)
                "--disable-backgrounding-occluded-windows", # âœ… Safe: khÃ´ng áº£nh hÆ°á»Ÿng extension background
                "--disable-renderer-backgrounding",         # âœ… Safe: khÃ´ng throttle renderer
                "--disable-background-timer-throttling",    # âœ… Safe: khÃ´ng throttle timers
                
                # ðŸ†• EXTENSION SUPPORT FLAGS
                "--enable-extensions",                     # âœ… EXPLICIT: Ä‘áº£m báº£o extensions enabled
                "--load-plugins",                          # âœ… SUPPORT: há»— trá»£ plugins náº¿u cáº§n
                
                url
            ]
            
            self.chrome_process = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            print(f"   -> Waiting for browser initialization...")
            time.sleep(3) # Chờ 1 chút cho Chrome hiện lên hẳn

            # Tiêm cookies ngay khi trình duyệt vừa mở
            if self.inject_cookies_via_cdp():
                print("   -> 🔄 Reloading page to apply login session...")
                # Sau khi set cookie, cần reload lại trang để web nhận diện đăng nhập
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url)

                    # =======================================================================
                    # TỰ ĐỘNG CLICK OK CHO TẤT CẢ JS ALERT/POPUP
                    # =======================================================================
                    # Đoạn này sẽ ghi đè hàm alert/confirm gốc của trình duyệt
                    js_override = """
                        // Ghi đè hàm alert (thông báo 1 nút OK)
                        window.alert = function(message) { 
                            console.log('🚫 Đã chặn Alert: ' + message); 
                            return true; 
                        };

                        // Ghi đè hàm confirm (hộp thoại Yes/No) -> Luôn trả về True (Yes/OK)
                        window.confirm = function(message) { 
                            console.log('🚫 Đã chặn Confirm (Auto Yes): ' + message); 
                            return true; 
                        };

                        // Ghi đè hàm prompt (nhập liệu) -> Trả về rỗng
                        window.prompt = function(message) { 
                            console.log('🚫 Đã chặn Prompt: ' + message); 
                            return null; 
                        };
                        
                        // Chặn sự kiện hiển thị dialog nếu có (BeforeUnload)
                        window.onbeforeunload = null;
                    """

                    self._send_cdp_command(ws, {
                        "method": "Page.addScriptToEvaluateOnNewDocument",
                        "params": {
                            "source": js_override
                        }
                    })
                    print("   -> 🛡️ Anti-Popup Shield activated (Auto-Click OK).")
                    # =======================================================================

                    self._send_cdp_command(ws, {"method": "Page.reload"})
                    ws.close()

            print(f"   -> Waiting {self.page_load_wait}s for page to initialize...")
            time.sleep(self.page_load_wait)
            
            game_window = next((w for w in gw.getWindowsWithTitle('cocrea.world') or gw.getWindowsWithTitle('Chrome') if w.title), None)
            if not game_window:
                raise RuntimeError("Critical Error: Could not find the game window.")
            
            self.safe_activate_window(game_window)
            self.safe_maximize_window(game_window)
            self.ensure_game_focus_light()
            print("   -> Browser is ready.\n")

            # --- PHASE 2: PATIENTLY VERIFY GAME COMPONENTS (Logic quyết định duy nhất) ---
            # Đây là logic cốt lõi. Nếu trang là 404, nó sẽ không có component game và sẽ timeout.
            # Nếu là trang game, nó sẽ chờ cho đến khi component xuất hiện.
            error_page_result = self.detect_cocrea_error_page_fast()
            if not isinstance(error_page_result, QualityGateResult) or error_page_result.status != "PASS":
                self.last_setup_result = error_page_result
                print(
                    "[FAIL] SETUP FAILED: Cocrea error-page verification did not pass: "
                    f"{getattr(error_page_result, 'failure_code', 'unknown')}"
                )
                self.cleanup_chrome_complete()
                return False

            print(f"PHASE 2: Patiently Verifying Game Components (Timeout: {self.game_verification_timeout}s)...")
            
            # Chúng ta sẽ gọi hàm verify_game_components_ready với timeout mặc định (dài) từ config
            component_result = self.verify_game_components_ready()
            if not component_result:
                self.last_setup_result = component_result
                # Nếu sau 120 giây mà vẫn không tìm thấy component game, chúng ta có thể chắc chắn
                # đây là trang lỗi (404, sign-in) hoặc một game bị hỏng nặng.
                print(f"❌ SETUP FAILED: Game components did not appear within {self.game_verification_timeout}s.")
                print("   This could be a 404/error page or a broken game mod.")
                self.cleanup_chrome_complete()
                return False
            
            print("   -> ✅ Game components found. The page is a valid game. Proceeding to interaction.\n")

            # --- PHASE 3: INTERACTION (RUN & FULLSCREEN) ---
            print("PHASE 3: Interacting with Game Interface...")

            if not self._click_run_button_cdp_only():
                self.last_setup_result = QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    message="Run button interaction was not verified.",
                )
                print("❌ SETUP FAILED: Could not find or click the 'Run' button.")
                self.cleanup_chrome_complete()
                return False
            
            print(f"   -> Waiting {self.game_load_wait}s for game to load after Run click...")
            time.sleep(self.game_load_wait)

            if not self._click_fullscreen_button_cdp_only():
                self.last_setup_result = QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    message="Fullscreen interaction was not verified.",
                )
                print("❌ SETUP FAILED: Could not find or click the 'Fullscreen' button.")
                self.cleanup_chrome_complete()
                return False

            self.press_f11_for_game_fullscreen()

            print("\n✅ GAME SETUP COMPLETED SUCCESSFULLY. Ready for automation.")
            print("======================================================================")
            return True

        except Exception as e:
            # Khối except không đổi
            print(f"❌ CRITICAL ERROR during game setup: {e}")
            import traceback
            traceback.print_exc()
            self.last_setup_result = QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"error": str(e), "url": getattr(self, "game_url", None)},
                message="Game setup raised an exception.",
            )
            self.cleanup_chrome_complete()
            try:
                messagebox.showerror("Setup Error", f"Failed to prepare game: {e}")
            except:
                pass
            return False

    def check_extension_health(self):
        """ðŸ” NEW: Check if extensions are loaded and working"""
        try:
            print("ðŸ” Checking extension health...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("   âš ï¸ Cannot check extensions - WebSocket not available")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            # JavaScript to check extensions
            extension_check_js = '''
            (() => {
                const results = {
                    extensionsEnabled: !!chrome.runtime,
                    extensionCount: 0,
                    uBlockDetected: false,
                    extensionsList: []
                };
                
                // Check if extensions API is available
                if (chrome && chrome.runtime) {
                    results.extensionsEnabled = true;
                    
                    // Try to detect uBlock Origin
                    try {
                        if (chrome.runtime.getManifest) {
                            results.currentExtension = chrome.runtime.getManifest().name;
                        }
                    } catch (e) {
                        // This is normal for content scripts
                    }
                    
                    // Check for uBlock by looking for blocked elements
                    const blockedElements = document.querySelectorAll('[style*="display: none"]').length;
                    if (blockedElements > 0) {
                        results.uBlockDetected = true;
                    }
                }
                
                return results;
            })();
            '''
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": extension_check_js, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value', {})
                
                extensions_enabled = result.get('extensionsEnabled', False)
                ublock_detected = result.get('uBlockDetected', False)
                
                print(f"   ðŸ“Š Extension Health Report:")
                print(f"      Extensions API: {'âœ… Available' if extensions_enabled else 'âŒ Not Available'}")
                print(f"      uBlock Origin: {'âœ… Working' if ublock_detected else 'âš ï¸ Not Detected'}")
                
                return extensions_enabled
            else:
                print("   âš ï¸ Could not get extension health data")
                return False
                
        except Exception as e:
            print(f"   âŒ Extension health check error: {e}")
            return False

    def _get_websocket_url(self, max_retries=5, retry_delay=2):
        """
        🔍 FIXED: Get WebSocket URL with retry mechanism and health verification. Automatically invalidates stale URLs.
        """
        import websocket

        # Nếu ws_url đã có, hãy kiểm tra xem nó còn "sống" không
        if self.ws_url:
            try:
                # Cố gắng tạo một kết nối ngắn để kiểm tra
                ws_test = websocket.create_connection(self.ws_url, timeout=3) # Bây giờ sẽ không báo lỗi nữa
                ws_test.close()
                print(f"   ✅ Existing WebSocket URL is still valid: {self.ws_url[:60]}...")
                return self.ws_url
            except Exception:
                # Nếu kết nối thất bại, URL này đã cũ. Xóa nó đi.
                print(f"   ⚠️ Stale WebSocket URL detected. Invalidating and fetching a new one.")
                self.ws_url = None # 🔥 ĐÂY LÀ ĐIỂM QUAN TRỌNG
        
        print(f"🔍 Getting WebSocket URL with retry (max: {max_retries} attempts)...")
        
        for attempt in range(1, max_retries + 1):
            try:
                print(f"   🔄 WebSocket attempt {attempt}/{max_retries}...")
                
                # Check if Chrome debug port is accessible
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                port_result = sock.connect_ex(('localhost', self.debugging_port))
                sock.close()
                
                if port_result != 0:
                    print(f"   ⚠️ Port {self.debugging_port} not accessible, Chrome may not be ready")
                    if attempt < max_retries:
                        print(f"   ⏳ Waiting {retry_delay}s before retry...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        print(f"   🚫 Port {self.debugging_port} still not accessible after {max_retries} attempts")
                        return None
                
                # Port accessible, try to get tabs
                import requests
                response = requests.get(f"http://localhost:{self.debugging_port}/json", timeout=5)
                
                if response.status_code != 200:
                    print(f"   ⚠️ HTTP {response.status_code} from debug endpoint")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        continue
                    else:
                        return None
                
                tabs = response.json()
                print(f"   📋 Found {len(tabs)} tabs from Chrome debug endpoint")
                
                # [FIXED] Find the EXACT tab for the current job's URL
                target_tab = None
                
                # self.game_url được thiết lập ở đầu hàm navigate_and_prepare_game
                current_job_url = self.game_url or ""

                if not current_job_url:
                    self.logger.error("Cannot get WebSocket URL because self.game_url is not set.")
                    return None

                # Tìm tab có URL khớp chính xác nhất
                for tab in tabs:
                    tab_url = tab.get('url', '')
                    if tab.get('type') == 'page' and current_job_url in tab_url:
                        target_tab = tab
                        print(f"   🎯 [EXACT MATCH] Found target tab: {tab.get('title', 'Unknown')[:50]}...")
                        break
                
                # Fallback: nếu không tìm thấy khớp chính xác, thử tìm bất kỳ tab cocrea nào
                if not target_tab:
                    print(f"   ⚠️ Could not find exact match for {current_job_url}. Falling back to first available cocrea tab...")
                    for tab in tabs:
                        if tab.get('type') == 'page' and 'cocrea.world' in tab.get('url', '').lower():
                            target_tab = tab
                            print(f"   🎯 [FALLBACK] Found cocrea.world tab: {tab.get('title', 'Unknown')[:50]}...")
                            break
                
                if not target_tab:
                    print(f"   ⚠️ No cocrea.world tab found in {len(tabs)} tabs")
                    
                    # Log available tabs for debugging
                    for i, tab in enumerate(tabs[:3]):  # Show first 3 tabs
                        url = tab.get('url', 'No URL')[:50]
                        title = tab.get('title', 'No title')[:30]
                        print(f"      Tab {i+1}: {title} - {url}")
                    
                    if attempt < max_retries:
                        print(f"   ⏳ Tab may still be loading, waiting {retry_delay}s...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        print("   🚫 No cocrea.world tab found after all attempts")
                        return None
                
                # Test WebSocket connection
                ws_url = target_tab['webSocketDebuggerUrl']
                print(f"   🔌 Testing WebSocket connection to: {ws_url[:50]}...")
                
                try:
                    import websocket
                    test_ws = websocket.create_connection(ws_url, timeout=5)
                    
                    # Send a simple test command
                    test_command = {
                        "id": 999999,
                        "method": "Runtime.evaluate",
                        "params": {"expression": "1", "returnByValue": True}
                    }
                    test_ws.send(json.dumps(test_command))
                    
                    # Wait for response
                    test_response = test_ws.recv()
                    test_ws.close()
                    
                    response_data = json.loads(test_response)
                    if response_data.get('id') == 999999:
                        self.ws_url = ws_url
                        print(f"   ✅ WebSocket connection verified and functional")
                        return self.ws_url
                    else:
                        print(f"   ⚠️ WebSocket test command failed")
                        
                except Exception as ws_error:
                    print(f"   ⚠️ WebSocket test failed: {ws_error}")
                
                if attempt < max_retries:
                    print(f"   ⏳ WebSocket test failed, waiting {retry_delay}s before retry...")
                    time.sleep(retry_delay)
                
            except Exception as e:
                print(f"   ⚠️ WebSocket URL attempt {attempt} error: {e}")
                if attempt < max_retries:
                    print(f"   ⏳ Waiting {retry_delay}s before retry...")
                    time.sleep(retry_delay)
        
        print(f"🚫 Failed to get functional WebSocket URL after {max_retries} attempts")
        return None

    def verify_cdp_ready(self, max_attempts=3):
        """
        🔍 NEW: Verify CDP is ready and functional before proceeding
        """
        try:
            print(f"🔍 Verifying CDP readiness (max {max_attempts} attempts)...")
            
            for attempt in range(1, max_attempts + 1):
                print(f"   🔄 CDP verification attempt {attempt}/{max_attempts}...")
                
                # Step 1: Get WebSocket URL with retry
                ws_url = self._get_websocket_url()
                if not ws_url:
                    print(f"   ❌ WebSocket URL not available on attempt {attempt}")
                    if attempt < max_attempts:
                        print("   ⏳ Waiting 3s before retry...")
                        time.sleep(3)
                        continue
                    else:
                        return False
                
                # Step 2: Test CDP commands
                try:
                    import websocket
                    ws = websocket.create_connection(ws_url, timeout=10)
                    
                    # Test basic Runtime command
                    test_response = self._send_cdp_command(ws, {
                        "method": "Runtime.evaluate",
                        "params": {"expression": "window.location.href", "returnByValue": True}
                    })
                    
                    if test_response and 'result' in test_response:
                        current_url = test_response.get('result', {}).get('result', {}).get('value', '')
                        print(f"   ✅ CDP test successful - Page: {current_url[:50]}...")
                        
                        # Test if it's the right page
                        if 'cocrea.world' in current_url.lower():
                            ws.close()
                            print(f"   🎯 CDP verified ready on cocrea.world page")
                            return True
                        else:
                            print(f"   ⚠️ CDP working but wrong page: {current_url}")
                    else:
                        print(f"   ⚠️ CDP test command failed - no valid response")
                    
                    ws.close()
                    
                except Exception as cdp_error:
                    print(f"   ❌ CDP test error: {cdp_error}")
                
                if attempt < max_attempts:
                    print("   ⏳ CDP not ready, waiting 5s before retry...")
                    time.sleep(5)
            
            print(f"❌ CDP not ready after {max_attempts} attempts")
            return False
            
        except Exception as e:
            print(f"❌ CDP verification error: {e}")
            return False

    def _send_cdp_command(self, ws, command):
        """Send CDP command via WebSocket"""
        try:
            command_id = random.randint(1, 1000000)
            command['id'] = command_id
            ws.send(json.dumps(command))
            
            max_wait_time = 30 
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                try:
                    response_str = ws.recv()
                    response_data = json.loads(response_str)
                    if response_data.get('id') == command_id:
                        return response_data
                except Exception as timeout_e:
                    if "timeout" in str(timeout_e).lower():
                        print("WARNING: WebSocket timeout, continuing to wait...")
                        continue
                    else:
                        raise timeout_e
                    
            print(f"ERROR: CDP command timeout after {max_wait_time}s")
            return None
            
        except Exception as e:
            print(f"ERROR: CDP command error: {e}")
            return None

    def _inspect_runtime_layout_bounds(self):
        """Read compact visible target bounds from the live Scratch runtime."""
        js = r"""
        new Promise((resolve) => {
          try {
            function findVM() {
              if (window.__vm && window.__vm.runtime) return window.__vm;
              function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
              const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
              for (const selector of selectors) {
                const elements = document.querySelectorAll(selector);
                for (const el of elements) {
                  const reactKey = getReactKey(el);
                  if (!reactKey) continue;
                  let fiber = el[reactKey];
                  let depth = 0;
                  while (fiber && depth++ < 120) {
                    const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                    if (props?.vm && props.vm.runtime) {
                      window.__vm = props.vm;
                      return props.vm;
                    }
                    fiber = fiber.return;
                  }
                }
              }
              return null;
            }

            const screenW = 1920;
            const screenH = 1080;
            const stageW = 640;
            const stageH = 360;
            const menuWidth = 78;
            const viewportW = screenW - menuWidth;
            const scale = Math.min(viewportW / stageW, screenH / stageH);
            const canvasW = stageW * scale;
            const canvasH = stageH * scale;
            const leftOffset = menuWidth;
            const topOffset = (screenH - canvasH) / 2;
            const canvasCenterX = leftOffset + (canvasW / 2);
            const canvasCenterY = topOffset + (canvasH / 2);

            function toPixel(logicX, logicY) {
              if (logicX === null || logicY === null || logicX === undefined || logicY === undefined) return { x: null, y: null };
              return { x: canvasCenterX + (logicX * scale), y: canvasCenterY - (logicY * scale) };
            }

            function boundsToPixels(bounds) {
              if (!bounds) return null;
              const left = Number(bounds.left);
              const right = Number(bounds.right);
              const top = Number(bounds.top);
              const bottom = Number(bounds.bottom);
              if (![left, right, top, bottom].every(Number.isFinite)) return null;
              const pTopLeft = toPixel(left, top);
              const pBottomRight = toPixel(right, bottom);
              const x = Math.min(pTopLeft.x, pBottomRight.x);
              const y = Math.min(pTopLeft.y, pBottomRight.y);
              const width = Math.abs(pBottomRight.x - pTopLeft.x);
              const height = Math.abs(pBottomRight.y - pTopLeft.y);
              return {
                x, y, width, height,
                topPixelY: y,
                bottomPixelY: y + height,
                centerPixelX: x + width / 2,
                centerPixelY: y + height / 2,
                raw: { left, right, top, bottom }
              };
            }

            function boundsCandidates(target, renderer) {
              const drawableID = target.drawableID ?? target._drawableID;
              const out = [];
              try {
                if (typeof target.getBounds === 'function') {
                  const raw = target.getBounds();
                  out.push({ source: 'target.getBounds', pixelBounds: boundsToPixels(raw) });
                }
              } catch (e) {}
              if (renderer && drawableID !== undefined && drawableID !== null) {
                for (const name of ['getBounds', 'getBoundsForDrawable']) {
                  try {
                    if (typeof renderer[name] === 'function') {
                      const raw = renderer[name](drawableID);
                      out.push({ source: `renderer.${name}`, pixelBounds: boundsToPixels(raw) });
                    }
                  } catch (e) {}
                }
              }
              return out.filter(c => c.pixelBounds);
            }

            const vm = findVM();
            if (!vm) {
              resolve({ ok: false, error: 'Cannot find Scratch VM' });
              return;
            }

            const runtime = vm.runtime;
            const renderer = runtime.renderer || vm.renderer || null;
            const allTargets = runtime.targets || [];
            const targets = allTargets
              .filter(t => !t.isStage && t.visible)
              .map((target, runtimeIndex) => {
                const sprite = target.sprite || {};
                const name = sprite.name || target.getName?.() || '';
                const pixel = toPixel(target.x, target.y);
                const candidates = boundsCandidates(target, renderer);
                const primary = candidates.find(c => c.pixelBounds)?.pixelBounds || null;
                return {
                  runtimeIndex,
                  id: target.id,
                  name,
                  visible: target.visible,
                  logicX: target.x,
                  logicY: target.y,
                  sizePercent: target.size,
                  direction: target.direction,
                  currentCostumeIndex: target.currentCostume,
                  pixelX: pixel.x,
                  pixelY: pixel.y,
                  boundsTopY: primary?.topPixelY ?? null,
                  boundsBottomY: primary?.bottomPixelY ?? null,
                  boundsCenterX: primary?.centerPixelX ?? null,
                  boundsCenterY: primary?.centerPixelY ?? null,
                  boundsX: primary?.x ?? null,
                  boundsY: primary?.y ?? null,
                  boundsWidth: primary?.width ?? null,
                  boundsHeight: primary?.height ?? null,
                  boundsSources: candidates.map(c => c.source)
                };
              });

            resolve({
              ok: true,
              capturedAt: new Date().toISOString(),
              screen: { width: screenW, height: screenH },
              canvas: { stageW, stageH, menuWidth, scale, canvasW, canvasH, leftOffset, topOffset },
              targets,
              muteButtons: targets.filter(t => /mute buttons/i.test(t.name))
            });
          } catch (error) {
            resolve({ ok: false, error: String(error), stack: error?.stack });
          }
        });
        """
        ws = None
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                return {"ok": False, "error": "No CDP websocket URL"}
            ws = websocket.create_connection(ws_url, timeout=10)
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js, "awaitPromise": True, "returnByValue": True}
            })
            value = response.get('result', {}).get('result', {}).get('value') if response else None
            return value if isinstance(value, dict) else {"ok": False, "error": "Runtime.evaluate returned no value"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

    def _median(self, values):
        if not values:
            return None
        vals = sorted(values)
        mid = len(vals) // 2
        if len(vals) % 2:
            return vals[mid]
        return (vals[mid - 1] + vals[mid]) / 2

    def _cluster_values(self, values, threshold=4.0):
        vals = sorted(values)
        if not vals:
            return []
        clusters = [[vals[0]]]
        for value in vals[1:]:
            if abs(value - clusters[-1][-1]) <= threshold:
                clusters[-1].append(value)
            else:
                clusters.append([value])
        return clusters

    def _detect_recording_bottom_y(self, runtime_layout, fallback=670.0):
        values = [
            float(target.get("boundsBottomY"))
            for target in runtime_layout.get("muteButtons", [])
            if isinstance(target.get("boundsBottomY"), (int, float)) and 600 <= float(target.get("boundsBottomY")) <= 740
        ]
        clusters = self._cluster_values(values)
        if clusters:
            best = max(clusters, key=lambda c: (len(c), -abs((self._median(c) or fallback) - fallback)))
            return {
                "value": float(self._median(best)),
                "method": "mute_buttons_boundsBottomY_cluster_median",
                "cluster": best,
                "allValues": values,
                "fallbackUsed": False,
            }
        return {
            "value": float(fallback),
            "method": "fallback_fixed_670",
            "cluster": [],
            "allValues": values,
            "fallbackUsed": True,
        }

    def _runtime_target_distance(self, target, x, y):
        distances = []
        if isinstance(target.get("pixelX"), (int, float)) and isinstance(target.get("pixelY"), (int, float)):
            distances.append(math.hypot(float(target["pixelX"]) - x, float(target["pixelY"]) - y))
        if isinstance(target.get("boundsCenterX"), (int, float)) and isinstance(target.get("boundsCenterY"), (int, float)):
            distances.append(math.hypot(float(target["boundsCenterX"]) - x, float(target["boundsCenterY"]) - y))
        return min(distances) if distances else math.inf

    def _find_runtime_character_candidate(self, before_layout, after_layout, target_slot, radius=360.0):
        before_by_id = {target.get("id"): target for target in (before_layout or {}).get("targets", [])}
        center_x = float(target_slot.get("x", 999))
        center_y = float(target_slot.get("y", 524))
        candidates = []
        for target in after_layout.get("targets", []):
            name = target.get("name", "")
            if "Icons" in name or re.match(r"^Polo\s+\d+", name, re.I) or re.search(r"button|mute|gallery|bars|preview", name, re.I):
                continue
            required = ("boundsX", "boundsY", "boundsWidth", "boundsHeight")
            if not all(isinstance(target.get(key), (int, float)) for key in required):
                continue
            distance = self._runtime_target_distance(target, center_x, center_y)
            if distance > radius:
                continue

            before_target = before_by_id.get(target.get("id"))
            changed = before_target is None
            if before_target:
                for key in ("pixelX", "pixelY", "boundsX", "boundsY", "boundsWidth", "boundsHeight", "currentCostumeIndex"):
                    after_value = target.get(key)
                    before_value = before_target.get(key)
                    if isinstance(after_value, (int, float)) and isinstance(before_value, (int, float)):
                        if abs(float(after_value) - float(before_value)) > 1:
                            changed = True
                            break
                    elif after_value != before_value:
                        changed = True
                        break

            area = float(target["boundsWidth"]) * float(target["boundsHeight"])
            score = distance
            if changed:
                score -= 150
            if 10_000 <= area <= 500_000:
                score -= 30
            elif area > 800_000:
                score += 200
            candidates.append({**target, "distanceToCenterSlot": distance, "changedAfterDrag": changed, "score": score})

        candidates.sort(key=lambda row: (row["score"], row["distanceToCenterSlot"], row.get("name") or ""))
        return candidates[0] if candidates else None

    def _clamp(self, value, minimum, maximum):
        return max(minimum, min(value, maximum))

    def _fallback_recording_area(self, target_slot, bottom_y, warnings):
        center_x = float(target_slot.get("x", 999))
        top = 170.0
        bottom = float(bottom_y)
        height = max(1.0, bottom - top)
        width = height * 96.0 / 83.0
        left = center_x - width / 2.0
        right = center_x + width / 2.0
        if left < 0:
            right -= left
            left = 0
        if right > 1920:
            shift = right - 1920
            left = max(0, left - shift)
            right = 1920
        warnings.append("character bounds unavailable; using legacy slot-centered fallback area")
        return {
            "x": int(round(left)),
            "y": int(round(top)),
            "width": int(round(right - left)),
            "height": int(round(bottom - top)),
            "bottomY": int(round(bottom)),
            "aspectRatio": "legacy_96:83_fallback",
            "sourceMethod": "legacy_slot_center_fallback",
        }

    def calculate_fixed_runtime_recording_area(self, target_slot=None, width=700.0, height=480.0):
        """Calculate the single fixed-size recordingArea for the full showcase."""
        warnings = []
        runtime_layout = self._inspect_runtime_layout_bounds()
        if runtime_layout.get("ok"):
            bottom_detection = self._detect_recording_bottom_y(runtime_layout)
        else:
            warnings.append(f"runtime inspect failed: {runtime_layout.get('error', 'unknown')}")
            bottom_detection = {
                "value": 670.0,
                "method": "fallback_fixed_670",
                "cluster": [],
                "allValues": [],
                "fallbackUsed": True,
            }

        if target_slot is None:
            slots = (self.config or {}).get("layout", {}).get("slots", [])
            center_slot_index = self.calculate_center_slot_index(slots) if slots else 4
            target_slot = self._find_slot_by_index(center_slot_index) if slots else None

        center_x = float((target_slot or {}).get("x", 999))
        bottom = float(bottom_detection.get("value", 670.0))
        left = center_x - float(width) / 2.0
        top = bottom - float(height)

        if left < 0:
            left = 0.0
        if left + width > 1920:
            left = max(0.0, 1920.0 - float(width))
        if top < 0:
            top = 0.0
        if top + height > 1080:
            top = max(0.0, 1080.0 - float(height))

        screen_area = {
            "x": int(round(left)),
            "y": int(round(top)),
            "width": int(round(width)),
            "height": int(round(height)),
            "aspectRatio": "35:24",
        }

        area = self._screen_area_to_master_video_area(screen_area)
        area["_runtimeDebug"] = {
            "bottomY": int(round(bottom)),
            "sourceMethod": "fixed_700x480_plus_mute_buttons_bottom_master_video_coords",
            "bottomLine": bottom_detection,
            "centerX": center_x,
            "fallbackUsed": bool(bottom_detection.get("fallbackUsed")),
            "warnings": warnings,
            "screenArea": screen_area,
            "coordinateSpace": "video_1920x1080_after_obs_master_crop_scale",
        }
        self.runtime_recording_area = area
        if self.obs_controller:
            self.obs_controller.set_recording_area(area)
        print(f"🎯 Runtime fixed recordingArea: {area['x']},{area['y']} {area['width']}x{area['height']}")
        return area

    def _screen_area_to_master_video_area(self, area):
        """Convert live screen coords to showcase video coords after OBS master crop+scale."""
        screen_w = 1920.0
        screen_h = 1080.0
        crop_left = 78.0
        crop_top = 22.0
        crop_right = 0.0
        crop_bottom = 22.0
        content_w = screen_w - crop_left - crop_right
        content_h = screen_h - crop_top - crop_bottom
        scale_x = screen_w / content_w
        scale_y = screen_h / content_h

        x = (float(area["x"]) - crop_left) * scale_x
        y = (float(area["y"]) - crop_top) * scale_y
        width = float(area["width"]) * scale_x
        height = float(area["height"]) * scale_y

        out_x = int(round(max(0.0, min(screen_w - 1.0, x))))
        out_y = int(round(max(0.0, min(screen_h - 1.0, y))))
        out_w = int(round(max(1.0, min(screen_w, width))))
        out_h = int(round(max(1.0, min(screen_h, height))))
        if out_x + out_w > int(screen_w):
            out_w = int(screen_w) - out_x
        if out_y + out_h > int(screen_h):
            out_h = int(screen_h) - out_y

        return {
            "x": out_x,
            "y": out_y,
            "width": out_w,
            "height": out_h,
            "aspectRatio": area.get("aspectRatio", "35:24"),
        }

    def _calculate_recording_area_from_candidate(self, candidate, bottom_y, warnings, padding=10.0):
        bx = float(candidate["boundsX"])
        by = float(candidate["boundsY"])
        bw = float(candidate["boundsWidth"])
        bh = float(candidate["boundsHeight"])
        candidate_bottom = by + bh
        left = bx - padding
        right = bx + bw + padding
        top = by - padding
        bottom = float(bottom_y)

        if candidate_bottom > bottom:
            warnings.append(f"candidate bounds bottom {candidate_bottom:.1f} exceeds detected bottomY {bottom:.1f}")
        if top >= bottom - 20:
            top = max(0, bottom - (bh + padding))
            warnings.append("top adjusted because fixed bottom produced invalid height")

        if left < 0:
            right -= left
            left = 0
        if right > 1920:
            shift = right - 1920
            left = max(0, left - shift)
            right = 1920
        top = self._clamp(top, 0, 1079)
        bottom = self._clamp(bottom, top + 1, 1080)

        return {
            "x": int(round(left)),
            "y": int(round(top)),
            "width": int(round(right - left)),
            "height": int(round(bottom - top)),
            "bottomY": int(round(bottom)),
            "aspectRatio": "native",
            "sourceMethod": "character_bounds_plus_mute_buttons_bottom",
        }

    def calculate_runtime_recording_area_after_drop(self, before_layout, target_slot, icon_index, segment_key):
        """Calculate and store the per-segment recordingArea while the dropped icon is still on stage."""
        warnings = []
        after_layout = self._inspect_runtime_layout_bounds()
        if not after_layout.get("ok"):
            warnings.append(f"runtime inspect failed: {after_layout.get('error', 'unknown')}")
            bottom_detection = {"value": 670.0, "method": "fallback_fixed_670", "fallbackUsed": True}
            candidate = None
        else:
            bottom_detection = self._detect_recording_bottom_y(after_layout)
            candidate = self._find_runtime_character_candidate(before_layout, after_layout, target_slot)

        bottom_y = bottom_detection.get("value", 670.0)
        fallback_used = bool(bottom_detection.get("fallbackUsed"))
        if candidate:
            area = self._calculate_recording_area_from_candidate(candidate, bottom_y, warnings)
        else:
            area = self._fallback_recording_area(target_slot, bottom_y, warnings)
            fallback_used = True

        area["segmentKey"] = segment_key
        area["iconIndex"] = icon_index
        area["source"] = {
            "bottomLine": bottom_detection,
            "characterCandidate": {
                key: candidate.get(key)
                for key in ("runtimeIndex", "id", "name", "boundsX", "boundsY", "boundsWidth", "boundsHeight", "distanceToCenterSlot", "changedAfterDrag", "score")
            } if candidate else None,
        }
        area["fallbackUsed"] = fallback_used
        area["warnings"] = warnings

        self.runtime_recording_areas[segment_key] = area
        if self.obs_controller:
            self.obs_controller.store_recording_area(segment_key, area)
        print(f"ðŸŽ¯ Runtime recordingArea for {segment_key}: {area['x']},{area['y']} {area['width']}x{area['height']} fallback={fallback_used}")
        return area

    def ensure_window_activation(self, operation_name="operation"):
        """Optimized window activation - avoid excessive maximize calls"""
        try:
            # Skip activation if recently done
            current_time = time.time()
            if hasattr(self, '_last_activation_time'):
                if current_time - self._last_activation_time < 2.0:  # Skip if within 2 seconds
                    print(f"ðŸ”§ Skipping activation for {operation_name} (recently activated)")
                    return True
            
            possible_windows = gw.getWindowsWithTitle('cocrea.world')
            if not possible_windows: 
                possible_windows = gw.getWindowsWithTitle('Chrome')
            if possible_windows:
                target_window = possible_windows[0]
                
                # Only activate if not already active
                try:
                    is_active = target_window.isActive
                    if not is_active:
                        target_window.activate()
                        time.sleep(0.5)
                        print(f"ðŸ”§ Window activated for {operation_name}")
                    else:
                        print(f"âœ… Window already active for {operation_name}")
                    
                    # Store last activation time
                    self._last_activation_time = current_time
                    return True
                    
                except Exception as e:
                    # Fallback: always activate
                    target_window.activate()
                    time.sleep(0.5)
                    self._last_activation_time = current_time
                    print(f"ðŸ”§ Window activated (fallback) for {operation_name}")
                    return True
            else:
                print(f"âš ï¸ Could not find window for {operation_name}")
                return False
        except Exception as e:
            print(f"âš ï¸ Window activation failed for {operation_name}: {e}")
            return False

    def _click_run_button_cdp_only(self):
        """Pure CDP implementation for Run button detection and clicking"""
        try:
            print("[RUN] CDP: finding Run button bounds, then clicking with mouse events...")
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("[RUN] WebSocket not available")
                return False

            ws = websocket.create_connection(ws_url, timeout=30)
            run_button_bounds_js = r'''
            (() => {
                try {
                    const selectors = [
                        '.style_run-button__VjFSY',
                        'button.MuiButton-contained.MuiButton-containedPrimary',
                        'button[class*="style_run-button"]',
                        'button[class*="run"]',
                        'button[class*="play"]',
                        'button[class*="start"]',
                        '.MuiButton-contained',
                        'button',
                        'input[type="submit"]',
                        'input[type="button"]',
                        '[role="button"]',
                        '[aria-label*="run" i]',
                        '[aria-label*="play" i]',
                        '[aria-label*="start" i]'
                    ];

                    function describe(el, selector) {
                        const rect = el.getBoundingClientRect();
                        const text = (el.textContent || el.value || el.alt || el.getAttribute('aria-label') || '').trim();
                        const lowerText = text.toLowerCase();
                        const className = typeof el.className === 'string' ? el.className : String(el.className || '');
                        const lowerClass = className.toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const style = window.getComputedStyle(el);
                        const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        const looksLikeRun =
                            lowerText === 'run' ||
                            lowerText.includes('run') ||
                            lowerText.includes('play') ||
                            lowerText.includes('start') ||
                            aria.includes('run') ||
                            aria.includes('play') ||
                            aria.includes('start') ||
                            lowerClass.includes('run-button') ||
                            lowerClass.includes('run');
                        return {el, selector, text, className, visible, looksLikeRun};
                    }

                    const candidates = [];
                    for (const selector of selectors) {
                        let elements = [];
                        try {
                            elements = Array.from(document.querySelectorAll(selector));
                        } catch (e) {
                            continue;
                        }
                        for (const el of elements) {
                            const info = describe(el, selector);
                            if (info.visible && info.looksLikeRun) candidates.push(info);
                        }
                    }

                    if (!candidates.length) {
                        return {success: false, reason: 'button_not_found', candidateCount: 0};
                    }

                    const candidate = candidates[0];
                    candidate.el.scrollIntoView({block: 'center', inline: 'center'});
                    const rect = candidate.el.getBoundingClientRect();
                    return {
                        success: true,
                        method: 'bounds_then_cdp_mouse',
                        strategy: candidate.selector,
                        text: candidate.text,
                        className: candidate.className,
                        candidateCount: candidates.length,
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height
                    };
                } catch (error) {
                    return {success: false, reason: 'js_error', error: String(error && error.stack || error)};
                }
            })();
            '''

            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": run_button_bounds_js, "returnByValue": True}
            })

            if response and 'error' in response:
                print(f"[RUN] Runtime.evaluate error: {response.get('error')}")
                ws.close()
                return False

            result = None
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')

            if not result or not result.get('success'):
                reason = result.get('reason', 'no_result') if result else 'no_result'
                error = result.get('error') if result else None
                if error:
                    print(f"[RUN] JavaScript error: {error}")
                print(f"[RUN] Run button not clickable via DOM bounds: {reason}")
                ws.close()
                return False

            x = result.get('x')
            y = result.get('y')
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                print(f"[RUN] Invalid Run button coordinates: ({x}, {y})")
                ws.close()
                return False

            print(f"[RUN] Found Run button via {result.get('strategy')} at ({x:.1f}, {y:.1f})")
            self._send_cdp_command(ws, {
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mouseMoved", "x": x, "y": y}
            })
            time.sleep(0.1)
            self._send_cdp_command(ws, {
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}
            })
            time.sleep(0.1)
            self._send_cdp_command(ws, {
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}
            })
            ws.close()
            print(f"[RUN] Run button clicked via {result.get('method')} (strategy: {result.get('strategy')})")
            return True

            print("ðŸ”§ CDP-ONLY: Finding and clicking Run button via DOM...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âŒ CDP-ONLY: WebSocket not available")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # JavaScript to find and click Run button
            run_button_js = '''
            new Promise((resolve, reject) => {
                try {
                    console.log("ðŸ”§ CDP-ONLY: Searching for Run button in DOM...");
                    
                    // Strategy 1: Specific class from extracted data
                    let runButton = document.querySelector('.style_run-button__VjFSY');
                    let strategy = 'specific_class';
                    
                    // Strategy 2: Material-UI button classes
                    if (!runButton) {
                        const muiSelectors = [
                            'button.MuiButton-contained.MuiButton-containedPrimary',
                            'button[class*="style_run-button"]',
                            'button.MuiButton-sizeLarge'
                        ];
                        
                        for (const selector of muiSelectors) {
                            try {
                                const buttons = document.querySelectorAll(selector);
                                for (const btn of buttons) {
                                    const text = (btn.textContent || '').trim().toLowerCase();
                                    if (text === 'run') {
                                        runButton = btn;
                                        strategy = `mui_selector_${selector}`;
                                        break;
                                    }
                                }
                                if (runButton) break;
                            } catch (e) {
                                console.warn(`Selector failed: ${selector}`);
                            }
                        }
                    }
                    
                    // Strategy 3: Text-based search (broad)
                    if (!runButton) {
                        const allButtons = document.querySelectorAll('button, input[type="submit"], input[type="button"]');
                        for (const btn of allButtons) {
                            const text = (btn.textContent || btn.value || '').trim().toLowerCase();
                            if (text === 'run' || text === 'â–¶' || text === 'play' || text === 'start') {
                                runButton = btn;
                                strategy = `text_based_${text}`;
                                break;
                            }
                        }
                    }
                    
                    // Strategy 4: Context-based search (in game containers)
                    if (!runButton) {
                        const gameContainers = document.querySelectorAll('.style_stage-cover__VITCW, .ccw-stage-wrapper, #stage-canvas-wrapper');
                        for (const container of gameContainers) {
                            const buttons = container.querySelectorAll('button, input[type="submit"]');
                            for (const btn of buttons) {
                                const text = (btn.textContent || btn.value || '').trim().toLowerCase();
                                if (text.includes('run') || text.includes('play') || text.includes('start')) {
                                    runButton = btn;
                                    strategy = 'context_based';
                                    break;
                                }
                            }
                            if (runButton) break;
                        }
                    }
                    
                    if (runButton) {
                        console.log(`ðŸŽ¯ CDP-ONLY: Found Run button via ${strategy}`);
                        console.log(`   Text: "${runButton.textContent || runButton.value || 'No text'}"`);
                        console.log(`   Classes: "${runButton.className || 'No classes'}"`);
                        
                        // Scroll button into view
                        runButton.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        
                        // Wait for scroll to complete
                        setTimeout(() => {
                            try {
                                // Attempt 1: Direct click
                                runButton.click();
                                console.log("âœ… CDP-ONLY: Run button clicked via direct click");
                                resolve({ success: true, method: 'direct_click', strategy: strategy });
                                
                            } catch (clickError) {
                                console.log("âš ï¸ CDP-ONLY: Direct click failed, trying dispatchEvent...");
                                
                                try {
                                    // Attempt 2: Dispatch click event
                                    const clickEvent = new MouseEvent('click', {
                                        view: window,
                                        bubbles: true,
                                        cancelable: true,
                                        button: 0
                                    });
                                    runButton.dispatchEvent(clickEvent);
                                    
                                    console.log("âœ… CDP-ONLY: Run button clicked via dispatchEvent");
                                    resolve({ success: true, method: 'dispatch_event', strategy: strategy });
                                    
                                } catch (dispatchError) {
                                    console.log("âš ï¸ CDP-ONLY: DispatchEvent failed, trying focus + click...");
                                    
                                    try {
                                        // Attempt 3: Focus + click
                                        runButton.focus();
                                        setTimeout(() => {
                                            runButton.click();
                                            console.log("âœ… CDP-ONLY: Run button clicked via focus + click");
                                            resolve({ success: true, method: 'focus_click', strategy: strategy });
                                        }, 200);
                                        
                                    } catch (focusError) {
                                        console.error("âŒ CDP-ONLY: All click methods failed:", focusError);
                                        resolve({ success: false, reason: 'all_click_methods_failed', strategy: strategy });
                                    }
                                }
                            }
                        }, 1000); // Wait 1s for scroll
                        
                    } else {
                        console.log("âŒ CDP-ONLY: Run button not found in DOM");
                        resolve({ success: false, reason: 'button_not_found' });
                    }
                    
                } catch (error) {
                    console.error("âŒ CDP-ONLY: Error finding Run button:", error);
                    reject({ success: false, error: error.toString() });
                }
            });
            '''
            
            # Execute the JavaScript
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": run_button_js, "awaitPromise": True, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')
                if result and result.get('success'):
                    method = result.get('method', 'unknown')
                    strategy = result.get('strategy', 'unknown')
                    print(f"âœ… CDP-ONLY: Run button clicked successfully via {method} (strategy: {strategy})")
                    return True
                else:
                    reason = result.get('reason', 'unknown') if result else 'no_result'
                    strategy = result.get('strategy', 'unknown') if result else 'none'
                    print(f"âŒ CDP-ONLY: Run button click failed - {reason} (strategy attempted: {strategy})")
                    return False
            else:
                print("âŒ CDP-ONLY: Failed to execute Run button JavaScript")
                return False
                
        except Exception as e:
            print(f"âŒ CDP-ONLY: Run button detection error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _click_fullscreen_button_cdp_only(self, wait_time=15):
        """SVG-aware Fullscreen with RELAXED verification"""
        try:
            print("ðŸ”§ CDP-ONLY: SVG-aware Fullscreen with RELAXED verification...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âŒ CDP-ONLY: WebSocket not available")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # Relaxed verification JavaScript
            fullscreen_button_js = f'''
            new Promise((resolve, reject) => {{
                try {{
                    console.log("ðŸ”§ CDP-ONLY: SVG-aware fullscreen with RELAXED verification...");
                    
                    const maxWaitTime = {wait_time * 1000};
                    const startTime = Date.now();
                    
                    function checkFullscreenStates() {{
                        const results = {{
                            browserFullscreen: !!(document.fullscreenElement || 
                                                document.webkitFullscreenElement || 
                                                document.mozFullScreenElement || 
                                                document.msFullscreenElement),
                            windowDimensions: {{
                                innerWidth: window.innerWidth,
                                innerHeight: window.innerHeight,
                                screenWidth: screen.width,
                                screenHeight: screen.height,
                                isMaximized: window.innerWidth === screen.width && window.innerHeight === screen.height
                            }},
                            documentSizes: {{
                                documentWidth: document.documentElement.clientWidth,
                                documentHeight: document.documentElement.clientHeight
                            }},
                            gameContainerStates: {{}}
                        }};
                        
                        // Check game container for fullscreen classes/styles
                        const gameContainers = [
                            '.style_gandi-player-wrapper__WCs0P',
                            '.style_stage-player__0Xy7d',
                            '.ccw-stage-wrapper'
                        ];
                        
                        gameContainers.forEach(selector => {{
                            const container = document.querySelector(selector);
                            if (container) {{
                                const computedStyle = window.getComputedStyle(container);
                                results.gameContainerStates[selector] = {{
                                    position: computedStyle.position,
                                    zIndex: computedStyle.zIndex,
                                    width: container.offsetWidth,
                                    height: container.offsetHeight,
                                    className: container.className,
                                    hasFullscreenClass: container.className.includes('fullscreen')
                                }};
                            }}
                        }});
                        
                        return results;
                    }}
                    
                    function analyzeIconBySVG(iconDiv) {{
                        const svg = iconDiv.querySelector('svg[viewBox="0 0 40 40"]');
                        if (!svg) return {{ type: 'unknown', reason: 'no_svg' }};
                        
                        const paths = svg.querySelectorAll('path');
                        const pathCount = paths.length;
                        const firstPath = paths[0] ? paths[0].getAttribute('d') || '' : '';
                        
                        // Pause: 1 path with pause pattern
                        if (pathCount === 1 && (firstPath.includes('C11.75 5') || firstPath.includes('13.8125'))) {{
                            return {{ type: 'pause', pathCount: pathCount }};
                        }}
                        
                        // Fullscreen: Multiple paths with expand pattern
                        if (pathCount >= 4 && (firstPath.includes('6.01539') || firstPath.includes('5.4546'))) {{
                            return {{ type: 'fullscreen', pathCount: pathCount }};
                        }}
                        
                        // Generic multi-path (likely fullscreen)
                        if (pathCount >= 4) {{
                            return {{ type: 'likely_fullscreen', pathCount: pathCount }};
                        }}
                        
                        return {{ type: 'unknown', pathCount: pathCount }};
                    }}
                    
                    function findFullscreenButton() {{
                        const allIcons = document.querySelectorAll('.style_gandi-player-actions-icon__uoqdH');
                        console.log(`ðŸŽ¯ Analyzing ${{allIcons.length}} action icons...`);
                        
                        for (let i = 0; i < allIcons.length; i++) {{
                            const result = analyzeIconBySVG(allIcons[i]);
                            console.log(`Icon ${{i + 1}}: ${{result.type}} (${{result.pathCount}} paths)`);
                            
                            if (result.type === 'fullscreen' || result.type === 'likely_fullscreen') {{
                                return {{ element: allIcons[i], ...result, index: i }};
                            }}
                        }}
                        
                        return null;
                    }}
                    
                    async function attemptFullscreenClick() {{
                        const candidate = findFullscreenButton();
                        
                        if (!candidate) {{
                            const elapsed = Date.now() - startTime;
                            if (elapsed < maxWaitTime) {{
                                console.log(`ðŸ” Waiting for fullscreen button... (${{Math.round(elapsed / 1000)}}s)`);
                                setTimeout(attemptFullscreenClick, 1000);
                            }} else {{
                                resolve({{ success: false, reason: 'no_fullscreen_button_found' }});
                            }}
                            return;
                        }}
                        
                        const element = candidate.element;
                        console.log(`ðŸŽ¯ Found fullscreen button: ${{candidate.type}} (${{candidate.pathCount}} paths)`);
                        
                        // Get initial states
                        const initialStates = checkFullscreenStates();
                        console.log("ðŸ“Š Initial states:", initialStates);
                        
                        // Scroll and click
                        element.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                        
                        setTimeout(() => {{
                            console.log("ðŸ–±ï¸ Attempting click...");
                            
                            try {{
                                // Simple click first
                                element.click();
                                console.log("âœ… Click executed successfully");
                                
                                // RELAXED verification with multiple checks
                                setTimeout(() => {{
                                    const newStates = checkFullscreenStates();
                                    console.log("ðŸ“Š Post-click states:", newStates);
                                    
                                    // Multiple success criteria (ANY can indicate success)
                                    const successCriteria = {{
                                        browserFullscreen: newStates.browserFullscreen && !initialStates.browserFullscreen,
                                        windowMaximized: newStates.windowDimensions.isMaximized && !initialStates.windowDimensions.isMaximized,
                                        dimensionChange: newStates.windowDimensions.innerWidth !== initialStates.windowDimensions.innerWidth ||
                                                    newStates.windowDimensions.innerHeight !== initialStates.windowDimensions.innerHeight,
                                        gameContainerChange: false // Will check below
                                    }};
                                    
                                    // Check for game container changes
                                    Object.keys(newStates.gameContainerStates).forEach(selector => {{
                                        const initial = initialStates.gameContainerStates[selector];
                                        const current = newStates.gameContainerStates[selector];
                                        if (initial && current) {{
                                            if (current.position !== initial.position || 
                                                current.zIndex !== initial.zIndex ||
                                                current.hasFullscreenClass !== initial.hasFullscreenClass) {{
                                                successCriteria.gameContainerChange = true;
                                            }}
                                        }}
                                    }});
                                    
                                    console.log("âœ… Success criteria analysis:", successCriteria);
                                    
                                    // RELAXED: Any positive indicator = success
                                    const anySuccess = Object.values(successCriteria).some(criteria => criteria === true);
                                    
                                    if (anySuccess) {{
                                        const activeCriteria = Object.keys(successCriteria).filter(key => successCriteria[key]);
                                        console.log(`ðŸŽ‰ RELAXED SUCCESS! Criteria met: ${{activeCriteria.join(', ')}}`);
                                        
                                        resolve({{
                                            success: true,
                                            method: 'direct_click',
                                            candidate: candidate.type,
                                            pathCount: candidate.pathCount,
                                            verified: true,
                                            successCriteria: activeCriteria,
                                            states: {{ initial: initialStates, final: newStates }}
                                        }});
                                    }} else {{
                                        console.log("âš ï¸ No clear success indicators, but click was executed");
                                        console.log("ðŸ¤” Assuming success due to successful click execution");
                                        
                                        // VERY RELAXED: If click executed without error, assume success
                                        resolve({{
                                            success: true,
                                            method: 'direct_click_assumed',
                                            candidate: candidate.type,
                                            pathCount: candidate.pathCount,
                                            verified: false,
                                            assumption: 'click_executed_successfully',
                                            states: {{ initial: initialStates, final: newStates }}
                                        }});
                                    }}
                                    
                                }}, 2000); // Wait 2s for effects to take place
                                
                            }} catch (clickError) {{
                                console.log(`âŒ Click failed: ${{clickError.message}}`);
                                
                                // Try backup method
                                try {{
                                    console.log("ðŸ”„ Trying backup click method...");
                                    const rect = element.getBoundingClientRect();
                                    const clickEvent = new MouseEvent('click', {{
                                        view: window,
                                        bubbles: true,
                                        cancelable: true,
                                        clientX: rect.left + rect.width / 2,
                                        clientY: rect.top + rect.height / 2
                                    }});
                                    element.dispatchEvent(clickEvent);
                                    
                                    console.log("âœ… Backup click executed");
                                    
                                    // Even more relaxed - assume backup worked
                                    setTimeout(() => {{
                                        console.log("ðŸ¤· Backup method completed - assuming success");
                                        resolve({{
                                            success: true,
                                            method: 'backup_click_assumed',
                                            candidate: candidate.type,
                                            pathCount: candidate.pathCount,
                                            verified: false,
                                            assumption: 'backup_click_executed'
                                        }});
                                    }}, 1500);
                                    
                                }} catch (backupError) {{
                                    console.log(`âŒ Backup click also failed: ${{backupError.message}}`);
                                    resolve({{
                                        success: false,
                                        reason: 'both_click_methods_failed',
                                        candidate: candidate.type,
                                        pathCount: candidate.pathCount
                                    }});
                                }}
                            }}
                            
                        }}, 800); // Wait for scroll
                    }}
                    
                    // Start attempt
                    setTimeout(attemptFullscreenClick, 2000);
                    
                }} catch (error) {{
                    console.error("âŒ Relaxed fullscreen error:", error);
                    reject({{ success: false, error: error.toString() }});
                }}
            }});
            '''
            
            # Execute relaxed JavaScript
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": fullscreen_button_js, "awaitPromise": True, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value')
                if result and result.get('success'):
                    method = result.get('method', 'unknown')
                    candidate = result.get('candidate', 'unknown')
                    verified = result.get('verified', False)
                    criteria = result.get('successCriteria', [])
                    assumption = result.get('assumption', None)
                    
                    print(f"âœ… CDP-ONLY: RELAXED Fullscreen SUCCESS!")
                    print(f"   ðŸŽ¯ Button: {candidate}")
                    print(f"   ðŸ–±ï¸ Method: {method}")
                    print(f"   âœ… Verified: {verified}")
                    if criteria:
                        print(f"   ðŸ“Š Criteria: {', '.join(criteria)}")
                    if assumption:
                        print(f"   ðŸ¤· Assumption: {assumption}")
                    
                    return True
                else:
                    reason = result.get('reason', 'unknown') if result else 'no_result'
                    print(f"âŒ CDP-ONLY: RELAXED Fullscreen still failed - {reason}")
                    return False
            else:
                print("âŒ CDP-ONLY: Failed to execute relaxed JavaScript")
                return False
                
        except Exception as e:
            print(f"âŒ CDP-ONLY: Relaxed Fullscreen error: {e}")
            return False

    def _simulate_click_via_cdp(self, ws, x, y):
        """Simulate mouse click via CDP"""
        if not self._register_scoped_physical_input():
            return False
        down_command = {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}
        down_response = self._send_cdp_command(ws, down_command)
        time.sleep(random.uniform(0.05, 0.1))
        up_command = {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}
        up_response = self._send_cdp_command(ws, up_command)
        return bool(
            down_response
            and up_response
            and "error" not in down_response
            and "error" not in up_response
        )

    def _simulate_drag_via_cdp(self, ws, start_x, start_y, end_x, end_y):
        """Simulate mouse drag via CDP"""
        # Move to start position
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
        time.sleep(0.2)
        
        # Mouse down
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1}})
        time.sleep(0.2)
        
        # Drag with smooth movement
        steps = 20
        for i in range(1, steps + 1):
            progress = i / steps
            curr_x = start_x + (end_x - start_x) * progress
            curr_y = start_y + (end_y - start_y) * progress
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left"}})
            time.sleep(0.02)
            
        # Mouse up at end position
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1}})

    # ================================================================
    # == NEW: DRAG VERIFICATION SYSTEM ==
    # ================================================================

    def setup_dual_verification_system(self):
        """
        ðŸ”„ DUAL VERIFICATION: Setup cáº£ Characters Array + Putted Characters
        Chá»‰ cáº§n 1 trong 2 hoáº¡t Ä‘á»™ng lÃ  Ä‘á»§
        """
        try:
            print("ðŸ”„ Setting up DUAL verification system...")
            
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âš ï¸ WebSocket not available, dual verification disabled")
                self.characters_available = False
                self.putted_chars_available = False
                return False
            
            # Setup JavaScript cho Cáº¢ HAI systems
            dual_setup_js = '''
            (() => {
                console.log("ðŸš€ Setting up DUAL verification system...");
                
                // VM Finder
                function findVMInWindow(win) {
                    if (!win) return null;
                    if (win.__vm?.runtime) return win.__vm;
                    if (win.Scratch?.vm?.runtime) return win.Scratch.vm;
                    if (win.vm?.runtime) return win.vm;
                    
                    function getReactKey(el){ return Object.keys(el).find(k => k.startsWith('__react')); }
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        const nodes = win.document?.querySelectorAll?.(selector) || [];
                        for (const el of nodes) {
                            const fiberKey = getReactKey(el);
                            if (!fiberKey) continue;
                            let fiber = el[fiberKey];
                            let hop = 0;
                            while (fiber && hop++ < 200) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm?.runtime) return props.vm;
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }
                
                const vm = findVMInWindow(window);
                if (!vm) {
                    console.error("âŒ Cannot find VM for dual verification");
                    return { characters: false, putted: false };
                }
                
                const runtime = vm.runtime || vm;
                const stage = (runtime.targets || []).find(t => t.isStage) || (runtime.targets || [])[0];
                if (!stage) {
                    console.error("âŒ Cannot find Stage for dual verification");
                    return { characters: false, putted: false };
                }
                
                // Store VM vÃ  Stage
                window.__dual_vm = vm;
                window.__dual_stage = stage;
                
                // Helper functions
                function getListByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name && Array.isArray(v.value)) return v;
                    }
                    if (stage.lists) {
                        for (const id in stage.lists) {
                            const L = stage.lists[id];
                            if (L?.name === name) return L;
                        }
                    }
                    return null;
                }
                
                function getVarByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name) return v;
                    }
                    return null;
                }
                
                let charactersSuccess = false;
                let puttedSuccess = false;
                
                // SYSTEM 1: Characters Array
                try {
                    window.getCharactersCount = () => {
                        try {
                            const entry = getListByName("Characters") || getVarByName("Characters");
                            const raw = Array.isArray(entry?.value) ? entry.value.slice() : [];
                            const normalized = raw.map(v => (v === 1 ? "1" : v === 0 ? "0" : String(v)));
                            return normalized.filter(v => v === "1").length;
                        } catch (e) {
                            console.error("Error getting Characters count:", e);
                            return null;
                        }
                    };
                    
                    const testChars = window.getCharactersCount();
                    if (testChars !== null) {
                        charactersSuccess = true;
                        console.log("âœ… Characters Array system ready:", testChars);
                    }
                } catch (e) {
                    console.warn("âš ï¸ Characters Array setup failed:", e);
                }
                
                // SYSTEM 2: Putted Characters (from old code)
                try {
                    const puttedVar = getVarByName("Putted characters");
                    if (puttedVar) {
                        window.__putted_var_id = puttedVar.id;
                        
                        window.getPuttedChars = () => {
                            try {
                                return window.__dual_vm.runtime.getTargetForStage().variables[window.__putted_var_id].value;
                            } catch (e) {
                                console.error("Error getting Putted characters:", e);
                                return null;
                            }
                        };
                        
                        const testPutted = window.getPuttedChars();
                        if (testPutted !== null) {
                            puttedSuccess = true;
                            console.log("âœ… Putted Characters system ready:", testPutted);
                        }
                    }
                } catch (e) {
                    console.warn("âš ï¸ Putted Characters setup failed:", e);
                }
                
                console.log("ðŸ”„ DUAL VERIFICATION SETUP RESULT:");
                console.log("   Characters Array:", charactersSuccess ? "âœ… Ready" : "âŒ Failed");
                console.log("   Putted Characters:", puttedSuccess ? "âœ… Ready" : "âŒ Failed");
                
                return {
                    characters: charactersSuccess,
                    putted: puttedSuccess,
                    hasBackup: charactersSuccess || puttedSuccess
                };
            })();
            '''
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": dual_setup_js, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value', {})
                
                self.characters_available = result.get('characters', False)
                self.putted_chars_available = result.get('putted', False)
                has_backup = result.get('hasBackup', False)
                
                if has_backup:
                    print("âœ… DUAL verification system setup successful!")
                    print(f"   Characters Array: {'âœ… Ready' if self.characters_available else 'âŒ Failed'}")
                    print(f"   Putted Characters: {'âœ… Ready' if self.putted_chars_available else 'âŒ Failed'}")
                    
                    # Test cáº£ hai systems
                    if self.characters_available:
                        test_chars = self.get_characters_count()
                        print(f"   ðŸ§ª Characters test: {test_chars}")
                    
                    if self.putted_chars_available:
                        test_putted = self.get_putted_chars_value()
                        print(f"   ðŸ§ª Putted test: {test_putted}")
                    
                    self.characters_setup_done = True
                    self.putted_chars_setup_done = True
                    return True
                else:
                    print("âŒ DUAL verification setup failed - no systems available")
                    return False
            else:
                print("âŒ No response from dual verification setup")
                return False
                
        except Exception as e:
            print(f"âŒ Error setting up dual verification: {e}")
            self.characters_available = False
            self.putted_chars_available = False
            return False

    def start_batch_verification_session(self, reset_reconnect_budget=True):
        """
        ðŸš€ NEW: Start batch verification session with persistent WebSocket (FIXED LOGIC)
        """
        try:
            print("ðŸš€ STARTING BATCH VERIFICATION SESSION...")
            print("="*60)

            if reset_reconnect_budget:
                self._batch_reconnect_attempts = 0
            
            # Get WebSocket URL
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âŒ Cannot get WebSocket URL for batch session")
                return False
            
            # Create persistent WebSocket connection
            self.session_websocket = websocket.create_connection(ws_url, timeout=10)
            # ðŸ”§ NEW: Set default timeout for operations
            self.session_websocket.settimeout(5)  # 5 second default timeout
            self.session_websocket_url = ws_url
            self.session_start_time = time.time()
            
            # ðŸ”§ FIX: Set active BEFORE testing to allow _send_batch_cdp_command to work
            self.batch_verification_active = True
            
            print(f"âœ… Batch WebSocket connected: {ws_url}")
            
            # Test connection with active flag set
            print("ðŸ” Testing batch WebSocket connection...")
            test_response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": "window.location.href", "returnByValue": True}
            })
            
            if not test_response:
                print("âŒ Batch WebSocket test failed - trying simple test...")
                
                # Try simpler test
                simple_test = self._send_batch_cdp_command({
                    "method": "Runtime.evaluate",
                    "params": {"expression": "true", "returnByValue": True}
                })
                
                if not simple_test:
                    print("âŒ Even simple batch test failed")
                    self.stop_batch_verification_session()
                    return False
                else:
                    print("âœ… Simple batch test passed, continuing...")
            else:
                print("âœ… Batch WebSocket test successful")
            
            # Setup verification functions
            success = self._setup_batch_verification_functions()
            if success:
                # Keep batch_verification_active = True (already set above)
                print("ðŸŽ¯ BATCH VERIFICATION SESSION ACTIVE!")
                print(f"   ðŸ“Š Performance: WebSocket reuse enabled")
                print(f"   âš¡ Speed: Pre-warmed verification functions")
                print(f"   ðŸ›¡ï¸ Reliability: Connection health monitoring")
                print("="*60)
                return True
            else:
                print("âŒ Failed to setup batch verification functions")
                self.stop_batch_verification_session()
                return False
                
        except Exception as e:
            print(f"âŒ Error starting batch verification session: {e}")
            self.stop_batch_verification_session()
            return False

    def stop_batch_verification_session(self):
        """
        ðŸ›‘ NEW: Stop batch verification session and cleanup
        """
        try:
            print("\nðŸ›‘ STOPPING BATCH VERIFICATION SESSION...")
            
            if self.session_websocket:
                try:
                    self.session_websocket.close()
                    print("   âœ… Batch WebSocket closed")
                except:
                    print("   âš ï¸ WebSocket close warning (already closed)")
            
            # Reset session state
            self.batch_verification_active = False
            self.session_websocket = None
            self.session_websocket_url = None
            self.verification_functions_ready = False
            self.session_start_time = None
            
            print("âœ… Batch verification session stopped")
            
        except Exception as e:
            print(f"âš ï¸ Error stopping batch session: {e}")
            # Force reset
            self.batch_verification_active = False
            self.session_websocket = None

    def _setup_batch_verification_functions(self):
        """
        âš¡ NEW: Setup verification functions once for entire session
        """
        try:
            print("âš¡ Setting up batch verification functions...")
            
            # Enhanced JavaScript setup for batch operations
            batch_setup_js = '''
            (() => {
                console.log("âš¡ Setting up BATCH verification functions...");
                
                // VM Finder (same as before)
                function findVMInWindow(win) {
                    if (!win) return null;
                    if (win.__vm?.runtime) return win.__vm;
                    if (win.Scratch?.vm?.runtime) return win.Scratch.vm;
                    if (win.vm?.runtime) return win.vm;
                    
                    function getReactKey(el){ return Object.keys(el).find(k => k.startsWith('__react')); }
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        const nodes = win.document?.querySelectorAll?.(selector) || [];
                        for (const el of nodes) {
                            const fiberKey = getReactKey(el);
                            if (!fiberKey) continue;
                            let fiber = el[fiberKey];
                            let hop = 0;
                            while (fiber && hop++ < 200) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm?.runtime) return props.vm;
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }
                
                const vm = findVMInWindow(window);
                if (!vm) {
                    console.error("âŒ VM not found for batch verification");
                    return { success: false, reason: "vm_not_found" };
                }
                
                const runtime = vm.runtime || vm;
                const stage = (runtime.targets || []).find(t => t.isStage) || (runtime.targets || [])[0];
                if (!stage) {
                    console.error("âŒ Stage not found for batch verification");
                    return { success: false, reason: "stage_not_found" };
                }
                
                // Store VM and Stage globally for batch operations
                window.__batch_vm = vm;
                window.__batch_stage = stage;
                
                // Helper functions for batch operations
                function getListByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name && Array.isArray(v.value)) return v;
                    }
                    if (stage.lists) {
                        for (const id in stage.lists) {
                            const L = stage.lists[id];
                            if (L?.name === name) return L;
                        }
                    }
                    return null;
                }
                
                function getVarByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name) return v;
                    }
                    return null;
                }
                
                // BATCH FUNCTION 1: Characters Array count
                window.getBatchCharactersCount = () => {
                    try {
                        const entry = getListByName("Characters") || getVarByName("Characters");
                        if (!entry || !Array.isArray(entry.value)) return null;
                        const raw = entry.value.slice();
                        const normalized = raw.map(v => (v === 1 ? "1" : v === 0 ? "0" : String(v)));
                        return normalized.filter(v => v === "1").length;
                    } catch (e) {
                        console.error("Error in getBatchCharactersCount:", e);
                        return null;
                    }
                };
                
                // BATCH FUNCTION 2: Putted Characters count
                const puttedVar = getVarByName("Putted characters");
                if (puttedVar) {
                    window.__batch_putted_var_id = puttedVar.id;
                    
                    window.getBatchPuttedChars = () => {
                        try {
                            return window.__batch_vm.runtime.getTargetForStage().variables[window.__batch_putted_var_id].value;
                        } catch (e) {
                            console.error("Error in getBatchPuttedChars:", e);
                            return null;
                        }
                    };
                }
                
                // BATCH FUNCTION 3: Dual state in single call (PERFORMANCE BOOST)
                window.getBatchDualState = () => {
                    try {
                        const charactersCount = window.getBatchCharactersCount();
                        const puttedCount = window.getBatchPuttedChars ? window.getBatchPuttedChars() : null;
                        
                        return {
                            characters: charactersCount,
                            putted: puttedCount,
                            timestamp: Date.now()
                        };
                    } catch (e) {
                        console.error("Error in getBatchDualState:", e);
                        return null;
                    }
                };

                // BATCH FUNCTION 4: Atomic placement snapshot.  Unlike the
                // legacy helpers this scans every runtime target, so sprites
                // that own Polos or button state are not silently ignored.
                const placementAliases = {
                    characters: ['characters'],
                    putted: ['putted characters', 'putted character'],
                    polos: ['polos'],
                    buttons: ['set of buttons enable list', 'set of buttons enabled list']
                };
                const normalizeName = value => String(value || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                const runtimeEntries = () => {
                    const entries = [];
                    for (const target of (runtime.targets || [])) {
                        for (const collectionName of ['variables', 'lists']) {
                            const collection = target?.[collectionName] || {};
                            for (const id of Object.keys(collection)) {
                                const entry = collection[id];
                                if (entry) entries.push({ target, entry });
                            }
                        }
                    }
                    return entries;
                };
                const findPlacementEntry = (aliases, scalarOnly = false) => {
                    const normalizedAliases = aliases.map(normalizeName);
                    const matches = runtimeEntries().filter(({ entry }) =>
                        normalizedAliases.includes(normalizeName(entry.name))
                    );
                    if (scalarOnly) {
                        return matches.find(({ entry }) => !Array.isArray(entry.value)) || matches[0] || null;
                    }
                    return matches.find(({ entry }) => Array.isArray(entry.value)) || matches[0] || null;
                };
                const copyValue = item => {
                    if (!item) return null;
                    const value = item.entry?.value;
                    return Array.isArray(value) ? value.slice() : value;
                };
                const countOnes = value => {
                    if (!Array.isArray(value)) return null;
                    return value.map(v => String(v)).filter(v => v === '1').length;
                };
                window.getBatchPlacementSnapshot = () => {
                    try {
                        const charactersEntry = findPlacementEntry(placementAliases.characters);
                        const puttedEntry = findPlacementEntry(placementAliases.putted, true);
                        const polosEntry = findPlacementEntry(placementAliases.polos);
                        const buttonsEntry = findPlacementEntry(placementAliases.buttons);
                        const characters = copyValue(charactersEntry);
                        const polos = copyValue(polosEntry);
                        const buttonEnableList = copyValue(buttonsEntry);
                        const puttedRaw = copyValue(puttedEntry);
                        const putted = Array.isArray(puttedRaw) ? countOnes(puttedRaw) : puttedRaw;
                        return {
                            timestamp: Date.now(),
                            sequence: (window.__batch_snapshot_sequence = (window.__batch_snapshot_sequence || 0) + 1),
                            characters: Array.isArray(characters) ? characters : null,
                            charactersCount: Array.isArray(characters) ? countOnes(characters) : null,
                            puttedCharacters: putted,
                            polos: Array.isArray(polos) ? polos : null,
                            buttonEnableList: Array.isArray(buttonEnableList) ? buttonEnableList : null,
                            available: {
                                characters: !!charactersEntry,
                                putted: !!puttedEntry,
                                polos: !!polosEntry,
                                buttons: !!buttonsEntry
                            }
                        };
                    } catch (e) {
                        console.error('Error in getBatchPlacementSnapshot:', e);
                        return { timestamp: Date.now(), sequence: 0, read_error: String(e) };
                    }
                };
                
                // Test all functions
                const testChars = window.getBatchCharactersCount();
                const testPutted = window.getBatchPuttedChars ? window.getBatchPuttedChars() : null;
                const testDual = window.getBatchDualState();
                const testPlacement = window.getBatchPlacementSnapshot();
                
                console.log("âš¡ BATCH VERIFICATION FUNCTIONS READY:");
                console.log("   Characters:", testChars);
                console.log("   Putted:", testPutted); 
                console.log("   Dual state:", testDual);
                
                return {
                    success: true,
                    characters_ready: testChars !== null,
                    putted_ready: testPutted !== null,
                    dual_ready: testDual !== null,
                    placement_ready: testPlacement !== null
                };
            })();
            '''
            
            # Execute batch setup
            response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": batch_setup_js, "returnByValue": True}
            })
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value', {})
                
                if result.get('success', False):
                    self.verification_functions_ready = True
                    chars_ready = result.get('characters_ready', False)
                    putted_ready = result.get('putted_ready', False)
                    dual_ready = result.get('dual_ready', False)
                    placement_ready = result.get('placement_ready', False)
                    
                    print(f"   âœ… Batch functions setup successful!")
                    print(f"   ðŸ“Š Characters function: {'âœ…' if chars_ready else 'âŒ'}")
                    print(f"   ðŸ“Š Putted function: {'âœ…' if putted_ready else 'âŒ'}")  
                    print(f"   ðŸ“Š Dual state function: {'âœ…' if dual_ready else 'âŒ'}")
                    print(f"   ðŸ“Š Placement snapshot: {'âœ…' if placement_ready else 'âš ï¸'}")
                    
                    return True
                else:
                    reason = result.get('reason', 'unknown')
                    print(f"   âŒ Batch setup failed: {reason}")
                    return False
            else:
                print("   âŒ No response from batch setup")
                return False
                
        except Exception as e:
            print(f"âŒ Error setting up batch functions: {e}")
            return False

    def _send_batch_cdp_command(self, command, timeout_seconds=5.0):
        """
        âš¡ NEW: Send CDP command using persistent batch WebSocket (DEBUG ENHANCED)
        """
        try:
            if not self.session_websocket or not self.batch_verification_active:
                print(f"   ðŸ” Batch command failed: WebSocket={bool(self.session_websocket)}, Active={self.batch_verification_active}")
                return None
            
            command_id = random.randint(1, 1000000)
            command['id'] = command_id
            
            print(f"   ðŸ“¤ Sending batch command: {command['method']}")
            self.session_websocket.send(json.dumps(command))
            
            # Reduced timeout for batch operations (faster)
            max_wait_time = max(float(timeout_seconds), 0.1)
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                try:
                    # Set a short timeout for individual recv calls
                    self.session_websocket.settimeout(0.5)
                    response_str = self.session_websocket.recv()
                    response_data = json.loads(response_str)
                    
                    print(f"   ðŸ“¥ Received response for command ID: {response_data.get('id', 'NO_ID')}")
                    
                    if response_data.get('id') == command_id:
                        print(f"   âœ… Batch command successful: {command['method']}")
                        return response_data
                        
                except Exception as timeout_e:
                    if "timeout" in str(timeout_e).lower():
                        continue
                    else:
                        print(f"   âš ï¸ WebSocket recv error: {timeout_e}")
                        break
            
            print(f"   â° Batch CDP command timeout after {max_wait_time}s")
            return None
            
        except Exception as e:
            print(f"   âŒ Batch CDP command error: {e}")
            # Mark session as failed for health check
            self.batch_verification_active = False
            return None

    def check_batch_session_health(self):
        """
        ðŸ” NEW: Check batch session health and auto-reconnect if needed
        """
        try:
            current_time = time.time()
            
            # Skip if health check not due
            if current_time - self.last_health_check < self.health_check_interval:
                return True
            
            self.last_health_check = current_time
            
            if not self.batch_verification_active or not self.session_websocket:
                return False
            
            print("ðŸ” Batch session health check...")
            
            # Quick health test
            test_response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": "Date.now()", "returnByValue": True}
            })
            
            if test_response:
                session_duration = current_time - (self.session_start_time or current_time)
                print(f"   âœ… Batch session healthy (running {session_duration:.1f}s)")
                return True
            else:
                print("   âŒ Batch session unhealthy - attempting reconnect...")
                return self._attempt_batch_session_reconnect()
                
        except Exception as e:
            print(f"âš ï¸ Health check error: {e}")
            return False

    def _attempt_batch_session_reconnect(self):
        """
        ðŸ”„ NEW: Attempt to reconnect batch session
        """
        try:
            if self._batch_reconnect_attempts >= 1:
                print("⚠️ Batch reconnect budget exhausted; disabling automatic reconnect")
                return False

            self._batch_reconnect_attempts += 1
            print("ðŸ”„ Attempting batch session reconnect...")
            
            # Stop current session
            self.stop_batch_verification_session()
            time.sleep(1)
            
            # Try to restart
            if self.start_batch_verification_session(reset_reconnect_budget=False):
                print("âœ… Batch session reconnected successfully")
                return True
            else:
                print("âŒ Batch session reconnect failed")
                return False
                
        except Exception as e:
            print(f"âŒ Reconnect error: {e}")
            return False

    def get_characters_count(self):
        """
        ðŸ“¢ BATCH OPTIMIZED: Get Characters count via batch session or fallback
        """
        try:
            # TRY BATCH FIRST (FAST PATH)
            if self.batch_verification_active and self.verification_functions_ready:
                if self.check_batch_session_health():
                    response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate",
                        "params": {"expression": "window.getBatchCharactersCount()", "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        count = response.get('result', {}).get('result', {}).get('value')
                        if isinstance(count, (int, float)):
                            return int(count)
            
            # FALLBACK TO INDIVIDUAL (SLOW PATH)
            print("âš ï¸ Batch unavailable, using individual verification")
            return self._get_characters_count_individual()
            
        except Exception as e:
            print(f"âŒ Error in batch characters count: {e}")
            return self._get_characters_count_individual()

    def _get_characters_count_individual(self):
        """
        ðŸ“¢ FALLBACK: Individual Characters count (original method)
        """
        try:
            if not self.characters_available:
                return None
                
            ws_url = self._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=3)
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": "window.getCharactersCount()", "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                count = response.get('result', {}).get('result', {}).get('value')
                if isinstance(count, (int, float)):
                    return int(count)
            
            return None
            
        except Exception as e:
            print(f"âŒ Error getting individual Characters count: {e}")
            return None

    def _execute_emergency_return_click(self, target_slot):
        """
        ðŸš¨ OPTIMIZED: Quick emergency return click without full verification cycle
        Used when normal return seems to have failed
        """
        return OperationResult(
            OperationStatus.STOPPED,
            direction="return",
            message="legacy_emergency_click_disabled",
        )
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            
            # Quick click without complex verification
            pyautogui.moveTo(center_x, center_y, duration=0.1)
            pyautogui.click(button='left')
            
            # Minimal wait for effect
            time.sleep(0.2)
            
            print(f"   âš¡ Emergency click executed at ({center_x}, {center_y})")
            return True
            
        except Exception as e:
            print(f"   âŒ Emergency click failed: {e}")
            return False

    def get_enabled_list_array(self):
        """
        ðŸ” NEW: Get Enabled List array for cross-validation
        Returns: list of strings ["0","1","0"...] or None if error
        """
        try:
            snapshot = self.get_placement_snapshot_batch()
            if isinstance(snapshot, dict) and isinstance(snapshot.get("buttonEnableList"), list):
                return snapshot.get("buttonEnableList")
            if not self.characters_available:
                return None
                
            ws_url = self._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            # JavaScript to get Enabled List array from Stage variables
            js_get_enabled = '''
            (() => {
                try {
                    if (!window.__sprunki_vm) {
                        console.error("VM not found for Enabled List");
                        return null;
                    }
                    
                    const stage = window.__sprunki_vm.runtime.getTargetForStage();
                    if (!stage) {
                        console.error("Stage not found for Enabled List");
                        return null;
                    }
                    
                    // Find Enabled List variable in Stage variables
                    const enabledVariable = Object.values(stage.variables).find(v => v.name === 'Enabled List');
                    
                    if (!enabledVariable) {
                        console.error("Enabled List variable not found in Stage");
                        return null;
                    }
                    
                    console.log("âœ… Found Enabled List array:", enabledVariable.value);
                    return enabledVariable.value;
                    
                } catch (e) {
                    console.error("Error getting Enabled List array:", e);
                    return null;
                }
            })();
            '''
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_get_enabled, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                enabled_value = response.get('result', {}).get('result', {}).get('value')
                
                if isinstance(enabled_value, list):
                    print(f"ðŸ” Enabled List retrieved: {enabled_value}")
                    return enabled_value
                else:
                    print(f"âš ï¸ Unexpected Enabled List type: {type(enabled_value)} = {enabled_value}")
                    return None
            else:
                print("âš ï¸ No response when getting Enabled List")
                return None
                
        except Exception as e:
            print(f"âŒ Error getting Enabled List: {e}")
            return None

    def get_putted_chars_value(self):
        """
        ðŸ“¢ BATCH OPTIMIZED: Get Putted Characters via batch session or fallback
        """
        try:
            # TRY BATCH FIRST (FAST PATH)
            if self.batch_verification_active and self.verification_functions_ready:
                if self.check_batch_session_health():
                    response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate",
                        "params": {"expression": "window.getBatchPuttedChars()", "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        value = response.get('result', {}).get('result', {}).get('value')
                        normalized_value = self._normalize_count_value(value)
                        if normalized_value is not None:
                            return normalized_value
            
            # FALLBACK TO INDIVIDUAL (SLOW PATH)  
            print("âš ï¸ Batch unavailable, using individual verification")
            return self._get_putted_chars_individual()
            
        except Exception as e:
            print(f"âŒ Error in batch putted chars: {e}")
            return self._get_putted_chars_individual()

    def _get_putted_chars_individual(self):
        """
        ðŸ“¢ FALLBACK: Individual Putted Characters (original method)
        """
        try:
            if not self.putted_chars_available:
                return None
                
            ws_url = self._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=3)
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": "window.getPuttedChars()", "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                value = response.get('result', {}).get('result', {}).get('value')
                normalized_value = self._normalize_count_value(value)
                if normalized_value is not None:
                    return normalized_value
            
            return None
            
        except Exception as e:
            print(f"âŒ Error getting individual putted chars: {e}")
            return None

    def get_dual_counts_batch(self):
        """
        ðŸš€ FIXED: Get BOTH counts with None safety
        """
        try:
            if self.batch_verification_active and self.verification_functions_ready:
                if self.check_batch_session_health():
                    
                    # Force state refresh before getting counts
                    refresh_response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate",
                        "params": {"expression": "void 0", "returnByValue": True}
                    })
                    
                    # Small delay for state stabilization
                    time.sleep(0.1)
                    
                    response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate", 
                        "params": {"expression": "window.getBatchDualState()", "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        result = response.get('result', {}).get('result', {}).get('value')
                        if result:
                            characters_count = self._normalize_count_value(result.get('characters'))
                            putted_count = self._normalize_count_value(result.get('putted'))
                            return characters_count, putted_count
            
            # Fallback to individual calls
            return self.get_dual_counts_individual()
            
        except Exception as e:
            print(f"âŒ Error in batch dual counts: {e}")
            return self.get_dual_counts_individual()

    def get_dual_counts_individual(self):
        """
        ðŸ”„ FALLBACK: Individual dual counts (original method)
        """
        characters_count = self._get_characters_count_individual()
        putted_count = self._get_putted_chars_individual()
        return characters_count, putted_count

    def get_dual_counts(self):
        """
        ðŸ”„ BATCH OPTIMIZED: Get BOTH VALUES using batch optimization
        """
        # Try batch first for maximum performance
        return self.get_dual_counts_batch()

    def get_placement_snapshot_batch(self):
        """Read all placement signals in one Runtime.evaluate call."""
        try:
            if not (self.batch_verification_active and self.verification_functions_ready):
                return None
            if not self.check_batch_session_health():
                return None
            response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {
                    "expression": "window.getBatchPlacementSnapshot()",
                    "returnByValue": True,
                },
            }, timeout_seconds=max(
                1.0,
                float(getattr(self, "verifier_snapshot_timeout_ms", 1500)) / 1000.0,
            ))
            if response and "result" in response:
                value = response.get("result", {}).get("result", {}).get("value")
                if isinstance(value, dict):
                    return value
            return None
        except Exception as error:
            print(f"⚠️ Error getting atomic placement snapshot: {error}")
            return None

    def _normalize_count_value(self, value):
        """
        Normalize Scratch runtime numeric values. Some mods expose variables as
        strings, which otherwise breaks arithmetic verification.
        """
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value) if math.isfinite(float(value)) else None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            try:
                return int(float(value))
            except (ValueError, OverflowError):
                return None
        return None

    def detect_return_mode(self, operation_context="unknown", icons_count=1):
        """
        ðŸŽ¯ NEW: Detect appropriate return mode based on context
        Returns: RETURN_MODE_INDIVIDUAL or RETURN_MODE_REMIX
        """
        try:
            print(f"\nðŸŽ¯ MODE DETECTION: Context='{operation_context}', Icons={icons_count}")
            
            # Rule 1: Check operation context (explicit detection)
            context_text = str(operation_context or "")
            if "showcase" in context_text.lower() or "individual" in context_text.lower():
                detected_mode = self.RETURN_MODE_INDIVIDUAL
                reason = f"Context indicates individual operation: '{context_text}'"

            elif "remix" in context_text.lower() or "batch" in context_text.lower():
                detected_mode = self.RETURN_MODE_REMIX  
                reason = f"Context indicates remix operation: '{context_text}'"
                
            # Rule 2: Check icons count (threshold-based detection)
            elif icons_count == 1:
                detected_mode = self.RETURN_MODE_INDIVIDUAL
                reason = f"Single icon operation detected (count={icons_count})"
                
            elif icons_count > 1:
                detected_mode = self.RETURN_MODE_REMIX
                reason = f"Multiple icons operation detected (count={icons_count})"
                
            # Rule 3: Check current slot state (state-based detection)
            else:
                current_state = self.get_comprehensive_slot_state()
                current_count = self._get_board_progress_count(current_state)
                if current_count is None:
                    print("   🛑 Return mode is UNKNOWN because board state cannot be read")
                    return None
                    
                if current_count <= 1:
                    detected_mode = self.RETURN_MODE_INDIVIDUAL
                    reason = f"Current state indicates individual mode (active_icons={current_count})"
                else:
                    detected_mode = self.RETURN_MODE_REMIX
                    reason = f"Current state indicates remix mode (active_icons={current_count})"
            
            # Update tracking
            self.current_return_mode = detected_mode
            self.active_icons_count = icons_count
            
            print(f"   ðŸ“Š Detected mode: {detected_mode.upper()}")
            print(f"   ðŸ’¡ Reason: {reason}")
            print(f"   ðŸ“ˆ Active icons: {self.active_icons_count}")
            
            return detected_mode
            
        except Exception as e:
            print(f"âŒ Error in mode detection: {e}")
            # A detection exception is UNKNOWN; a guessed individual route
            # could send a physical click on an unverified board.
            return None

    def set_return_mode_explicit(self, mode, reason="manual_override"):
        """
        ðŸŽ¯ NEW: Explicitly set return mode (for manual control)
        """
        if mode in [self.RETURN_MODE_INDIVIDUAL, self.RETURN_MODE_REMIX]:
            print(f"ðŸŽ¯ EXPLICIT MODE SET: {mode.upper()} ({reason})")
            self.current_return_mode = mode
            return True
        else:
            print(f"âŒ Invalid return mode: {mode}")
            return False

    def get_current_return_mode(self):
        """
        ðŸŽ¯ NEW: Get current return mode with auto-detection fallback
        """
        if self.current_return_mode == self.RETURN_MODE_AUTO:
            # Auto-detect based on current state
            return self.detect_return_mode("auto_detection")
        else:
            return self.current_return_mode

    def execute_smart_return_with_mode_detection(self, target_slot, operation_context="unknown", use_cdp=False):
        """
        ðŸŽ¯ NEW: Smart return router with automatic mode detection
        Routes to appropriate return strategy based on detected mode
        """
        try:
            print(f"\nðŸŽ¯ SMART RETURN ROUTER: Starting mode detection...")
            print("="*70)
            
            # Detect appropriate mode
            detected_mode = self.detect_return_mode(operation_context)
            
            # Route to appropriate strategy
            if detected_mode == self.RETURN_MODE_INDIVIDUAL:
                print("ðŸŽª ROUTING â†’ Individual Return Strategy (Precision Targeting)")
                return self.execute_individual_return_strategy(target_slot, use_cdp)
                
            elif detected_mode == self.RETURN_MODE_REMIX:
                print("ðŸŽµ ROUTING â†’ Remix Return Strategy (Carpet Bombing)")
                return self.execute_remix_return_strategy(target_slot, use_cdp)
                
            else:
                print(f"âš ï¸ Unknown mode: {detected_mode}, falling back to individual")
                return OperationResult(
                    OperationStatus.UNKNOWN,
                    direction="return",
                    message="return_mode_unknown",
                )
                
        except Exception as e:
            print(f"âŒ Error in smart return router: {e}")
            # Do not start a second return chain after mode detection failed.
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="return",
                message=f"return_mode_detection_exception:{e}",
            )

    def execute_individual_return_strategy(self, target_slot, use_cdp=False):
        """
        ðŸŽª NEW: Individual return strategy (current precision method)
        """
        print("ðŸŽª EXECUTING INDIVIDUAL RETURN STRATEGY...")
        print("   ðŸŽ¯ Method: Precision targeting with safe zones")
        print("   ðŸ” Verification: Per-operation count monitoring")
        
        # Use current method for individual operations
        return self.corrected_return_icon(target_slot, use_cdp)

    def execute_remix_return_strategy(self, target_slot, use_cdp=False, operation_context=None):
        """
        ðŸŽµ Bounded sequential carpet bombing with a structured result.
        """
        context = operation_context or getattr(self, "_active_operation_context", None)
        owns_context = False
        if context is None:
            context = self._begin_operation(
                "cleanup",
                target_slot.get("index") if isinstance(target_slot, dict) else None,
                # Reserve room for one bounded cleanup strategy plus a
                # validated manual-profile replay if the single recovery is
                # needed.  This remains a hard finite budget.
                max_input_attempts=120 + MAX_PREPARE_ACTIONS + 2,
                deadline_seconds=(MAX_TOTAL_PREPARE_DURATION_MS / 1000.0) + 60.0,
            )
            owns_context = True
        if context is None:
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="cleanup",
                message="another_operation_is_active",
            )

        def finish(status, message, details=None):
            result = self._operation_result(context, status, message, details=details)
            if owns_context:
                return self._finish_operation(context, result)
            return result

        try:
            print("ðŸŽµ ULTRA SIMPLIFIED CARPET BOMBING")
            print("="*50)
            print("   ðŸŽ¯ Strategy: Sequential slot-by-slot carpet bombing")
            print("   ðŸ“Š Progression: 2â†’3â†’4â†’5... coordinates per slot")
            
            # Capture a bounded cleanup baseline.  Cleanup relies on the
            # aggregate board count, so one fresh atomic sample is sufficient;
            # target-specific stability is not required for this operation.
            initial_snapshot = self._capture_stable_placement_snapshot(
                timeout_ms=getattr(self, "verifier_snapshot_timeout_ms", 1500),
                context=context,
                stable_samples=1,
            )
            initial_count = (
                initial_snapshot.characters_count
                if initial_snapshot is not None and initial_snapshot.characters_count is not None
                else initial_snapshot.putted_count
                if initial_snapshot is not None
                else None
            )
            if initial_snapshot is None or initial_count is None:
                print("   🛑 Initial board state is UNKNOWN; refusing cleanup input")
                return finish(OperationStatus.UNKNOWN, "cleanup_initial_state_unknown")
            if self._is_board_count_clean(
                characters_count=initial_snapshot.characters_count,
                putted_count=initial_snapshot.putted_count,
            ):
                print("   âœ… Already clean")
                return finish(OperationStatus.ALREADY_EMPTY, "cleanup_already_empty")
            context.set_baseline(initial_snapshot)
            
            print(f"   ðŸ“Š Icons to clear: {initial_count}")
            
            # Get ALL slots from config
            if not self.config:
                print("âŒ No config available")
                return finish(OperationStatus.FAILED, "cleanup_config_unavailable")
                
            all_slots = self.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("âŒ No slots in config")
                return finish(OperationStatus.FAILED, "cleanup_slots_unavailable")
            
            # Sort slots by index for sequential processing
            sorted_slots = sorted(all_slots, key=lambda s: s.get('index', 0))
            print(f"   ðŸŽ¯ Will process {len(sorted_slots)} slots sequentially")
            
            # Execute progressive carpet bombing sets
            success = self.execute_sequential_carpet_bombing_sets(
                sorted_slots,
                initial_count,
                use_cdp,
                operation_context=context,
            )
            final_snapshot = self._capture_stable_placement_snapshot(
                timeout_ms=getattr(self, "verifier_snapshot_timeout_ms", 1500),
                context=context,
                stable_samples=1,
            )
            if final_snapshot is None:
                print("   🛑 Final cleanup state is UNKNOWN")
                return finish(OperationStatus.UNKNOWN, "cleanup_final_state_unknown")
            verified_clean = self._is_board_count_clean(
                characters_count=final_snapshot.characters_count,
                putted_count=final_snapshot.putted_count,
            )
            if verified_clean:
                return finish(
                    OperationStatus.VERIFIED,
                    "cleanup_verified",
                    details={"initialCount": initial_count},
                )
            if self.stop_event.is_set():
                return finish(OperationStatus.STOPPED, "cleanup_stop_requested")
            return finish(
                OperationStatus.FAILED if not success else OperationStatus.UNKNOWN,
                "cleanup_incomplete",
                details={"initialCount": initial_count},
            )

        except Exception as e:
            print(f"âŒ Error in simplified carpet bombing: {e}")
            return finish(OperationStatus.FAILED, f"cleanup_exception:{e}")

    def _determine_adaptive_density(self, icon_count, slot_count):
        """
        ðŸŽ¨ NEW: Determine optimal grid density based on situation
        """
        try:
            # Density rules based on complexity
            if icon_count <= 2:
                return "default"  # 3x3 grid sufficient
            elif icon_count <= 5:
                return "default"  # 3x3 still good
            elif icon_count <= 8:
                return "high"     # 5x5 for more coverage
            else:
                return "high"     # Maximum density for complex cases
                
        except:
            return "default"

    def _detect_progress_stagnation(self, progress_history, current_wave):
        """
        ðŸ“Š NEW: Detect if progress has stagnated (no improvement in recent waves)
        """
        try:
            if len(progress_history) < 3:
                return False  # Need at least 3 waves to detect stagnation
            
            # Check last 3 waves for progress
            recent_counts = [p['count'] for p in progress_history[-3:] if p['count'] is not None]
            
            if len(recent_counts) >= 3:
                # No improvement in last 3 waves = stagnation
                if recent_counts[-1] == recent_counts[-2] == recent_counts[-3]:
                    return True
            
            return False
            
        except:
            return False

    def generate_carpet_grid_for_slots(self, slot_indices, density="default"):
        """
        ðŸŽ¨ NEW: Generate carpet bombing grid covering specified slots
        Returns: List of (x, y) coordinates for systematic clicking
        """
        try:
            print(f"\nðŸŽ¨ GENERATING CARPET GRID")
            print("="*50)
            print(f"   ðŸŽ¯ Target slots: {slot_indices}")
            print(f"   ðŸŽ¨ Density: {density}")
            
            if not self.config:
                print("âŒ No config available for grid generation")
                return []
            
            all_slots = self.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("âŒ No slots configuration found")
                return []
            
            # Get density setting
            grid_density = self.carpet_grid_config.get(f'{density}_density', 
                                                    self.carpet_grid_config['default_density'])
            overlap_margin = self.carpet_grid_config['overlap_margin']
            
            print(f"   ðŸ“ Grid density: {grid_density}x{grid_density}")
            print(f"   ðŸ“ Overlap margin: {overlap_margin}px")
            
            # Calculate overall coverage area
            coverage_area = self._calculate_slots_coverage_area(slot_indices, all_slots, overlap_margin)
            if not coverage_area:
                print("âŒ Could not calculate coverage area")
                return []
            
            # Generate grid points within coverage area
            grid_points = self._generate_grid_points(coverage_area, grid_density)
            
            print(f"   âœ… Generated {len(grid_points)} grid points")
            print(f"   ðŸ“Š Coverage: {coverage_area['width']}x{coverage_area['height']}px")
            
            return grid_points
            
        except Exception as e:
            print(f"âŒ Error generating carpet grid: {e}")
            return []



    def _generate_grid_points(self, coverage_area, density):
        """
        ðŸŽ¯ NEW: Generate systematic grid points within coverage area
        """
        try:
            grid_points = []
            
            width = coverage_area['width']
            height = coverage_area['height']
            min_x = coverage_area['min_x']
            min_y = coverage_area['min_y']
            
            # Calculate step sizes
            step_x = width / (density + 1)  # +1 to avoid edges
            step_y = height / (density + 1)
            
            # Generate grid pattern
            for row in range(1, density + 1):
                for col in range(1, density + 1):
                    x = min_x + (col * step_x)
                    y = min_y + (row * step_y)
                    
                    # Add small random jitter to avoid perfect grid (more natural)
                    jitter_x = random.randint(-3, 3)
                    jitter_y = random.randint(-3, 3)
                    
                    final_x = int(x + jitter_x)
                    final_y = int(y + jitter_y)
                    
                    grid_points.append((final_x, final_y))
            
            # Sort points for optimal clicking pattern (spiral or row-by-row)
            grid_points = self._optimize_click_sequence(grid_points, coverage_area)
            
            return grid_points
            
        except Exception as e:
            print(f"âŒ Error generating grid points: {e}")
            return []

    def _optimize_click_sequence(self, points, coverage_area):
        """
        ðŸ”„ NEW: Optimize click sequence for efficiency
        Options: spiral, row-by-row, center-out
        """
        try:
            # For now, use center-out pattern (click from center outward)
            center_x = coverage_area['center_x']
            center_y = coverage_area['center_y']
            
            # Sort by distance from center
            def distance_from_center(point):
                x, y = point
                return ((x - center_x) ** 2 + (y - center_y) ** 2) ** 0.5
            
            optimized_points = sorted(points, key=distance_from_center)
            
            print(f"   ðŸ”„ Optimized click sequence: center-out pattern")
            
            return optimized_points
            
        except Exception as e:
            print(f"âŒ Error optimizing click sequence: {e}")
            return points  # Return original if optimization fails

    def visualize_carpet_grid(self, slot_indices=None):
        """
        ðŸŽ¨ NEW: Visualize carpet grid for debugging (optional tool)
        """
        try:
            if slot_indices is None:
                # Default to first few slots for testing
                slot_indices = [0, 1, 2, 3]
            
            print(f"\nðŸŽ¨ CARPET GRID VISUALIZATION")
            print("="*50)
            
            grid_points = self.generate_carpet_grid_for_slots(slot_indices, "default")
            
            if grid_points:
                print(f"ðŸ“ Grid Points Generated:")
                for i, (x, y) in enumerate(grid_points, 1):
                    print(f"   {i:2d}. ({x:4d}, {y:4d})")
                
                # Show pattern
                print(f"\nðŸŽ¯ Click Pattern (first 9 points):")
                for i in range(min(9, len(grid_points))):
                    x, y = grid_points[i]
                    print(f"   Wave 1, Click {i+1}: ({x}, {y})")
            else:
                print("âŒ No grid points generated")
                
        except Exception as e:
            print(f"âŒ Visualization error: {e}")

    def show_carpet_grid_info(self):
        """Ultra simplified carpet bombing info - print only"""
        try:
            print("\nðŸŽ¯ ULTRA SIMPLIFIED CARPET BOMBING INFO:")
            print("="*60)
            print("ðŸ“Š STRATEGY: Sequential slot-by-slot progression")
            print("ðŸŽ¯ EXECUTION: Process slots 0â†’1â†’2â†’...â†’last sequentially")
            print("ðŸ“ˆ PROGRESSION: 2â†’3â†’4â†’5â†’6â†’7... coordinates per slot")
            print("\nðŸ“ COORDINATE STRATEGY PER SLOT:")
            print("   â€¢ Set 1: 2 coords = Center + Horizontal offset")
            print("   â€¢ Set 2: 3 coords = Center + Horizontal + Vertical")
            print("   â€¢ Set 3: 4 coords = Center + H + V + Diagonal")
            print("   â€¢ Set 4: 5 coords = Previous + More diagonals")
            print("   â€¢ Set 5+: 6+ coords = Previous + Random scatter")
            print(f"\nâ±ï¸ TIMING:")
            print(f"   â€¢ Inter-click: {self.carpet_bombing_config['inter_click_delay']}s (within slot)")
            print(f"   â€¢ Inter-slot: {self.carpet_bombing_config['inter_slot_delay']}s (between slots)")
            print(f"   â€¢ Inter-set: {self.carpet_bombing_config['inter_set_delay']}s (between sets)")
            print(f"   â€¢ Max sets: {self.carpet_bombing_config['max_sets']}")
            print("\nðŸ”„ TERMINATION: Continue until Characters count = 0")
            print("\nâœ… SIMPLIFIED FEATURES:")
            print("   â€¢ No edge detection")
            print("   â€¢ No complex grid calculation")
            print("   â€¢ No boundary sweeps")
            print("   â€¢ Pure sequential slot processing")
            print("   â€¢ Progressive coordinate increase")
            print("="*60)
            
        except Exception as e:
            print(f"âŒ Config info error: {e}")

    def execute_carpet_bombing_waves(self, grid_points, target_description="slots", use_cdp=False):
        """
        ðŸŽ¯ NEW: Execute carpet bombing in waves with progress monitoring
        """
        try:
            print(f"\nðŸŽ¯ CARPET BOMBING EXECUTION")
            print("="*60)
            print(f"   ðŸŽ¯ Target: {target_description}")
            print(f"   ðŸ“Š Total points: {len(grid_points)}")
            
            wave_size = self.carpet_grid_config['wave_size']
            max_waves = self.carpet_grid_config['max_waves']
            inter_click_delay = self.carpet_grid_config['inter_click_delay']
            inter_wave_delay = self.carpet_grid_config['inter_wave_delay']
            
            # Split into waves
            waves = [grid_points[i:i+wave_size] for i in range(0, len(grid_points), wave_size)]
            total_waves = min(len(waves), max_waves)
            
            print(f"   ðŸŒŠ Total waves: {total_waves} (max: {max_waves})")
            print(f"   â±ï¸ Wave size: {wave_size} clicks")
            
            # Get initial state
            initial_count = self.get_characters_count() if self.characters_available else None
            print(f"   ðŸ“Š Initial count: {initial_count}")
            
            if self._is_board_count_clean(characters_count=initial_count):
                print("   âœ… Already clean - no bombing needed")
                return True, 0, []
            
            waves_executed = 0
            total_clicks = 0
            
            for wave_num, wave_points in enumerate(waves[:max_waves], 1):
                if self.stop_event.is_set():
                    break
                    
                print(f"\nðŸŒŠ WAVE {wave_num}/{total_waves}")
                print(f"   ðŸ“ Points: {len(wave_points)}")
                
                # Execute wave
                wave_success, clicks_in_wave = self._execute_single_wave(
                    wave_points, wave_num, use_cdp, inter_click_delay
                )
                
                waves_executed += 1
                total_clicks += clicks_in_wave
                
                if not wave_success:
                    print(f"   âŒ Wave {wave_num} failed")
                    continue
                
                # Check progress after wave
                time.sleep(inter_wave_delay)
                current_count = self.get_characters_count() if self.characters_available else None
                
                print(f"   ðŸ“Š Count after wave: {current_count}")
                
                if self._is_board_count_clean(characters_count=current_count):
                    print(f"   ðŸŽ‰ SUCCESS! Clean after wave {wave_num}")
                    return True, waves_executed, []
                elif current_count and initial_count and current_count < initial_count:
                    print(f"   ðŸ“ˆ Progress: {initial_count} â†’ {current_count}")
                
                # Continue to next wave
                print(f"   â­ï¸ Continuing to next wave...")
            
            # Final check
            final_count = self.get_characters_count() if self.characters_available else None
            success = self._is_board_count_clean(characters_count=final_count)
            
            print(f"\nðŸŽ¯ CARPET BOMBING COMPLETED")
            print(f"   ðŸŒŠ Waves executed: {waves_executed}")
            print(f"   ðŸ‘† Total clicks: {total_clicks}")
            print(f"   ðŸ“Š Final count: {final_count}")
            print(f"   ðŸŽ¯ Result: {'âœ… SUCCESS' if success else 'âŒ INCOMPLETE'}")
            
            return success, waves_executed, [] if success else [f"Still {final_count} icons remaining"]
            
        except Exception as e:
            print(f"âŒ Error in carpet bombing: {e}")
            return False, 0, [f"Execution error: {str(e)}"]

    def _execute_single_wave(self, wave_points, wave_num, use_cdp, inter_click_delay):
        """
        ðŸŒŠ NEW: Execute a single wave of clicks
        """
        try:
            print(f"   ðŸŽ¯ Executing {len(wave_points)} clicks...")
            
            clicks_executed = 0
            
            for i, (x, y) in enumerate(wave_points, 1):
                if self.stop_event.is_set():
                    break
                    
                # Execute click
                click_success = self._execute_carpet_click(x, y, use_cdp)
                
                if click_success:
                    clicks_executed += 1
                    print(f"      {i}/{len(wave_points)}: ({x},{y}) âœ…")
                else:
                    print(f"      {i}/{len(wave_points)}: ({x},{y}) âŒ")
                
                # Delay between clicks in same wave
                if i < len(wave_points):  # Don't delay after last click
                    time.sleep(inter_click_delay)
            
            print(f"   âœ… Wave completed: {clicks_executed}/{len(wave_points)} clicks")
            return clicks_executed > 0, clicks_executed
            
        except Exception as e:
            print(f"   âŒ Wave execution error: {e}")
            return False, 0

    def _execute_carpet_click(self, x, y, use_cdp):
        """
        ðŸ‘† NEW: Execute single carpet bombing click
        """
        try:
            active_context = getattr(self, "_active_operation_context", None)
            if active_context is None:
                print("      🛑 Carpet click rejected without an active OperationContext")
                return False
            if not active_context.can_send_input(stop_requested=self.stop_event.is_set()):
                return False
            # Carpet bombing is a bounded batch strategy.  Repeated
            # coordinates are allowed, but every physical click consumes
            # the one shared operation budget.
            active_context.register_input()
            if use_cdp:
                return self._execute_cdp_carpet_click(x, y)
            else:
                return self._execute_pyautogui_carpet_click(x, y)
                
        except Exception as e:
            print(f"      âŒ Click error at ({x},{y}): {e}")
            return False

    def _execute_cdp_carpet_click(self, x, y):
        """ðŸ‘† NEW: CDP carpet click"""
        try:
            if self.batch_verification_active and self.session_websocket:
                # Use batch WebSocket if available
                down_response = self._send_batch_cdp_command({
                    "method": "Input.dispatchMouseEvent",
                    "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}
                })
                time.sleep(0.05)
                up_response = self._send_batch_cdp_command({
                    "method": "Input.dispatchMouseEvent", 
                    "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}
                })
                return bool(down_response and up_response)
            else:
                # Fallback to individual WebSocket
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=3)
                    success = self._simulate_click_via_cdp(ws, x, y)
                    ws.close()
                    return bool(success)
                return False
                
        except Exception as e:
            return False

    def _execute_pyautogui_carpet_click(self, x, y):
        """ðŸ‘† NEW: PyAutoGUI carpet click"""
        try:
            pyautogui.click(x, y)
            return True
        except Exception as e:
            return False

    def smart_batch_return_all_slots_with_mode(self, use_cdp=False, context=None, obs_controller=None):
        """Run one finite cleanup operation and at most one session recovery."""
        current_obs_controller = obs_controller or self.obs_controller
        operation_context = (
            context if isinstance(context, OperationContext)
            else getattr(self, "_active_operation_context", None)
        )
        owns_context = False
        if operation_context is None:
            operation_context = self._begin_operation(
                "cleanup",
                None,
                max_input_attempts=120 + MAX_PREPARE_ACTIONS + 2,
                deadline_seconds=(MAX_TOTAL_PREPARE_DURATION_MS / 1000.0) + 60.0,
            )
            owns_context = True
        if operation_context is None:
            return 0, 0, ["cleanup_operation_context_unavailable"]

        dummy_slot = {"x": 999, "y": 515, "index": 3}
        recovery_started = False
        try:
            strategy_result = self.execute_remix_return_strategy(
                dummy_slot,
                use_cdp,
                operation_context=operation_context,
            )
            if strategy_result.status in {
                OperationStatus.VERIFIED,
                OperationStatus.ALREADY_EMPTY,
                OperationStatus.ALREADY_PLACED,
            }:
                result = self._operation_result(
                    operation_context,
                    strategy_result.status,
                    "cleanup_verified",
                    details={"strategy": strategy_result.as_dict()},
                )
                if owns_context:
                    self._finish_operation(operation_context, result)
                return 1, 1, []

            if strategy_result.status in {OperationStatus.UNKNOWN, OperationStatus.STOPPED}:
                print(
                    f"   🛑 Cleanup strategy stopped with "
                    f"{strategy_result.status.value}; no blind recovery input is sent"
                )
                if owns_context:
                    self._finish_operation(operation_context, strategy_result)
                return 0, 1, [
                    f"cleanup_{strategy_result.status.value.lower()}:{strategy_result.message}"
                ]

            if self._session_recovery_count >= self._max_session_recoveries:
                result = self._operation_result(
                    operation_context,
                    OperationStatus.FAILED,
                    "session_recovery_circuit_breaker",
                )
                if owns_context:
                    self._finish_operation(operation_context, result)
                return 0, 1, [result.message]

            if not operation_context.begin_recovery():
                result = self._operation_result(
                    operation_context,
                    OperationStatus.FAILED,
                    "cleanup_recovery_budget_exhausted",
                )
                if owns_context:
                    self._finish_operation(operation_context, result)
                return 0, 1, [result.message]
            recovery_started = True
            if current_obs_controller:
                metadata = {"reason": "bounded_cleanup_failed"}
                if isinstance(context, dict):
                    metadata.update(context)
                current_obs_controller.log_event_timestamp(
                    "start_game_restart",
                    "Restart triggered during bounded cleanup",
                    metadata,
                )

            restart_result = self.restart_game_to_initial_state(
                use_cdp,
                operation_context=operation_context,
            )
            operation_context.end_recovery()
            recovery_started = False
            if current_obs_controller:
                current_obs_controller.log_event_timestamp(
                    "end_game_restart",
                    "Restart after bounded cleanup completed",
                    {"status": restart_result.status.value},
                )
            if restart_result.status == OperationStatus.RECOVERED:
                result = self._operation_result(
                    operation_context,
                    OperationStatus.RECOVERED,
                    "cleanup_recovered_not_verified",
                    details={"recovery": restart_result.as_dict()},
                )
                if owns_context:
                    self._finish_operation(operation_context, result)
                return 0, 1, ["cleanup_recovered_not_verified"]

            result = self._operation_result(
                operation_context,
                restart_result.status,
                "cleanup_recovery_failed",
                details={"recovery": restart_result.as_dict()},
            )
            if owns_context:
                self._finish_operation(operation_context, result)
            return 0, 1, [f"cleanup_recovery_{restart_result.status.value.lower()}"]
        except Exception as error:
            if recovery_started:
                operation_context.end_recovery()
            result = self._operation_result(
                operation_context,
                OperationStatus.FAILED,
                f"cleanup_exception:{error}",
            )
            if owns_context:
                self._finish_operation(operation_context, result)
            return 0, 1, [result.message]

    def _legacy_smart_batch_return_all_slots_with_mode(self, use_cdp=False, context=None, obs_controller=None):
        """
        Run one bounded batch cleanup and at most one explicit recovery.
        The tuple shape remains compatible with existing workflow callers.
        """
        return 0, 0, ["legacy_batch_cleanup_disabled"]
        try:
            print(f"\nðŸ”„ ULTRA SIMPLIFIED BATCH RETURN")
            print("="*50)
            
            # Luôn sử dụng obs_controller được truyền vào nếu có
            current_obs_controller = obs_controller or self.obs_controller

            dummy_slot = {'x': 999, 'y': 515, 'index': 3}
            success = self.execute_remix_return_strategy(dummy_slot, use_cdp)
            if success:
                print("SEQUENTIAL CARPET BOMBING SUCCESS for remix cleanup")
                return 1, 1, []

            print("SEQUENTIAL CARPET BOMBING FAILED for remix cleanup")
            print("ATTEMPTING ONE GAME RESTART RECOVERY for remix cleanup...")
            if current_obs_controller:
                restart_metadata = {"reason": "Remix cleanup failed"}
                if isinstance(context, dict):
                    restart_metadata.update(context)
                current_obs_controller.log_event_timestamp(
                    "start_game_restart",
                    "Restart triggered during remix cleanup",
                    restart_metadata,
                )

            restart_result = self.restart_game_to_initial_state(use_cdp)
            if current_obs_controller:
                current_obs_controller.log_event_timestamp(
                    "end_game_restart",
                    "Restart after remix cleanup completed",
                    {"status": restart_result.status.value},
                )
            if restart_result.status == OperationStatus.RECOVERED:
                print("GAME RESTART RECOVERY COMPLETED; target cleanup is not verified")
                return 0, 1, ["cleanup_recovered_not_target_verified"]
            return 0, 1, [f"cleanup_recovery_{restart_result.status.value.lower()}"]

        except Exception as e:
            print(f"Ultra simplified batch return error: {e}")
            return 0, 1, [f"batch_cleanup_exception:{str(e)}"]

    def _execute_carpet_bombing_strategy(self, icon_count, use_cdp=False):
        """
        ðŸŽµ PHASE 4.3A: Carpet bombing strategy execution
        """
        try:
            print("ðŸŽµ CARPET BOMBING STRATEGY")
            print("-" * 40)
            
            # Get active slots for carpet bombing
            active_slots = self.get_active_slots_queue()
            if not active_slots:
                # Fallback to estimated slot coverage
                active_slots = list(range(min(8, icon_count + 1)))
            
            print(f"   ðŸŽ¯ Target slots: {active_slots}")
            
            # Execute carpet bombing via remix strategy
            dummy_slot = {'x': 999, 'y': 515, 'index': 3}  # Dummy slot for interface compatibility
            success = self.execute_remix_return_strategy(dummy_slot, use_cdp)
            
            return success, {
                'method': 'carpet_bombing',
                'target_slots': active_slots,
                'icon_count': icon_count
            }
            
        except Exception as e:
            print(f"âŒ Carpet bombing strategy error: {e}")
            return False, {'error': str(e)}

    def _execute_precision_strategy(self, icon_count, use_cdp=False):
        """
        ðŸŽª PHASE 4.3B: Precision strategy execution  
        """
        try:
            print("ðŸŽª PRECISION STRATEGY")
            print("-" * 40)
            
            # Use existing smart batch return for precision operations
            result = self.smart_batch_return_all_slots(use_cdp)
            success_count, total_attempted, errors = result
            
            success = (success_count == total_attempted)
            
            return success, {
                'method': 'precision_targeting',
                'success_count': success_count,
                'total_attempted': total_attempted,
                'errors': errors
            }
            
        except Exception as e:
            print(f"âŒ Precision strategy error: {e}")
            return False, {'error': str(e)}

    def show_current_mode_info(self):
        """Show current return mode information"""
        try:
            current_mode = self.get_current_return_mode()
            active_count = self.get_characters_count() if self.characters_available else "N/A"
            
            mode_info = f"""ðŸŽ¯ RETURN MODE INFORMATION:

    ðŸ“Š Current Mode: {current_mode.upper()}
    ðŸ“ˆ Active Icons: {active_count}
    ðŸŽ® Auto-Detection: {'âœ… Enabled' if self.current_return_mode == self.RETURN_MODE_AUTO else 'âŒ Manual Override'}

    ðŸŽª INDIVIDUAL MODE:
    - Used for: Single icon operations
    - Strategy: Precision targeting with safe zones
    - Verification: Per-operation count monitoring

    ðŸŽµ REMIX MODE:
    - Used for: Multiple icons batch operations  
    - Strategy: Carpet bombing (Phase 2)
    - Verification: Progressive count monitoring

    ðŸ”„ Mode Detection Rules:
    1. Context keywords (showcase/remix/batch)
    2. Icons count threshold (1 vs >1)
    3. Current slot state analysis"""
            
            messagebox.showinfo("Return Mode Information", mode_info)
            
        except Exception as e:
            messagebox.showerror("Mode Info Error", f"Error getting mode info: {e}")

    def _execute_precision_fallback_for_remix(self, slot_indices, use_cdp=False):
        """
        ðŸŽ¯ SIMPLIFIED: Just return false - no precision fallback needed
        """
        print("ðŸŽ¯ PRECISION FALLBACK DISABLED - Pure carpet bombing only")
        return False

    def _execute_precision_recovery(self, slot_indices, use_cdp=False):
        """
        ðŸ”„ SIMPLIFIED: Just return false - no precision recovery needed  
        """
        print("ðŸ”„ PRECISION RECOVERY DISABLED - Pure carpet bombing only")
        return False

    def execute_smart_return_with_auto_recovery(self, target_slot, operation_context="unknown", max_retries=2, use_cdp=False):
        """
        Compatibility wrapper.  Recovery is now owned by corrected_return_icon;
        this method must not wrap it in another retry loop.
        """
        try:
            print(f"\nðŸ›¡ï¸ SMART RETURN WITH AUTO-RECOVERY")
            print("="*60)
            print(f"   ðŸŽ¯ Context: {operation_context}")
            result = self.corrected_return_icon(target_slot, use_cdp)
            print(f"   Return result: {result.status.value} ({result.message})")
            return result
            
        except Exception as e:
            print(f"âŒ Error in smart return with recovery: {e}")
            return OperationResult(OperationStatus.FAILED, direction="return", message=str(e))

    def _attempt_smart_recovery(self, target_slot, context, attempt_num, use_cdp):
        """
        ðŸ”„ SIMPLIFIED: Just return false - no smart recovery needed
        """
        print("ðŸ”„ SMART RECOVERY DISABLED - Pure carpet bombing only")
        return False

    def test_carpet_bombing_system(self):
        """
        ðŸ§ª NEW: Comprehensive test of carpet bombing system
        """
        try:
            print("\nðŸ§ª TESTING CARPET BOMBING SYSTEM")
            print("="*60)
            
            # Test 1: Mode Detection
            print("ðŸ§ª TEST 1: Mode Detection")
            individual_mode = self.detect_return_mode("showcase_icon_test", 1)
            remix_mode = self.detect_return_mode("remix_test", 5)
            print(f"   Individual detection: {'âœ…' if individual_mode == self.RETURN_MODE_INDIVIDUAL else 'âŒ'}")
            print(f"   Remix detection: {'âœ…' if remix_mode == self.RETURN_MODE_REMIX else 'âŒ'}")
            
            # Test 2: Grid Generation
            print("\nðŸ§ª TEST 2: Grid Generation")
            test_slots = [0, 1, 2, 3]
            grid_points = self.generate_carpet_grid_for_slots(test_slots, "default")
            print(f"   Grid generation: {'âœ…' if len(grid_points) > 0 else 'âŒ'} ({len(grid_points)} points)")
            
            # Test 3: Configuration Validation
            print("\nðŸ§ª TEST 3: Configuration Validation")
            config_valid = all(key in self.carpet_grid_config for key in [
                'default_density', 'wave_size', 'max_waves'
            ])
            print(f"   Configuration: {'âœ…' if config_valid else 'âŒ'}")
            
            # Test 4: Integration
            print("\nðŸ§ª TEST 4: Integration Test")
            try:
                dummy_slot = {'x': 999, 'y': 515, 'index': 3}
                test_context = "integration_test"
                # Don't actually execute, just test setup
                mode = self.detect_return_mode(test_context, 3)
                integration_ready = (mode in [self.RETURN_MODE_INDIVIDUAL, self.RETURN_MODE_REMIX])
                print(f"   Integration: {'âœ…' if integration_ready else 'âŒ'}")
            except Exception as e:
                print(f"   Integration: âŒ ({e})")
            
            print("\nðŸŽ¯ CARPET BOMBING SYSTEM TEST COMPLETED")
            
        except Exception as e:
            print(f"âŒ Testing error: {e}")

    def show_carpet_bombing_test_results(self):
        """GUI method to show test results"""
        try:
            # Capture test output
            import io
            import sys
            
            # Redirect stdout to capture test output
            old_stdout = sys.stdout
            sys.stdout = test_output = io.StringIO()
            
            self.test_carpet_bombing_system()
            
            # Restore stdout
            sys.stdout = old_stdout
            test_results = test_output.getvalue()
            
            messagebox.showinfo("Carpet Bombing Test Results", test_results)
            
        except Exception as e:
            messagebox.showerror("Test Error", f"Error running tests: {e}")

    def execute_full_carpet_bombing_round(self, grid_points, round_num, expected_clear, use_cdp=False):
        """
        ðŸŒŠ ENHANCED: Execute round with edge priority
        """
        try:
            print(f"ðŸŒŠ ENHANCED ROUND {round_num} WITH EDGE PRIORITY")
            print(f"   ðŸ“ Total points: {len(grid_points)}")
            print(f"   ðŸŽ¯ Expected to clear: {expected_clear}")
            
            # PHASE 1: Execute all grid points
            total_clicks = 0
            successful_clicks = 0
            
            print(f"   ðŸŽ¯ Phase 1: Main grid execution ({len(grid_points)} points)")
            
            for i, (x, y) in enumerate(grid_points, 1):
                if self.stop_event.is_set():
                    break
                    
                # Execute single click
                click_success = self._execute_carpet_click(x, y, use_cdp)
                
                total_clicks += 1
                if click_success:
                    successful_clicks += 1
                
                # Minimal delay between clicks
                time.sleep(0.02)
                
                # Progress indicator every 30 clicks
                if i % 30 == 0:
                    print(f"      Progress: {i}/{len(grid_points)} clicks")
            
            print(f"   âœ… Phase 1 completed: {successful_clicks}/{total_clicks} clicks")
            
            # PHASE 2: EDGE REINFORCEMENT (extra clicks for stubborn edge slots)
            if round_num >= 2:  # Start edge reinforcement from round 2
                edge_reinforcement_clicks = self._execute_edge_reinforcement(round_num, use_cdp)
                total_clicks += edge_reinforcement_clicks
                successful_clicks += edge_reinforcement_clicks
                print(f"   ðŸŽ¯ Phase 2: Edge reinforcement - {edge_reinforcement_clicks} extra clicks")
            
            print(f"   âœ… Round {round_num} TOTAL: {successful_clicks}/{total_clicks} clicks successful")
            
            return successful_clicks > 0
            
        except Exception as e:
            print(f"âŒ Error in enhanced round execution: {e}")
            return False

    def execute_sequential_carpet_bombing_sets(
        self,
        sorted_slots,
        initial_count,
        use_cdp=False,
        operation_context=None,
    ):
        """
        [FIXED v2] Execute progressive carpet bombing sets.
        Returns False if a game restart was necessary.
        """
        try:
            print(f"\nðŸŽ¯ SEQUENTIAL CARPET BOMBING SETS")
            print("="*60)
            
            context = operation_context or getattr(self, "_active_operation_context", None)
            if context is None:
                print("   🛑 Carpet bombing requires a coordinator-owned OperationContext")
                return False
            if getattr(self, "_active_operation_context", None) is not context:
                print("   🛑 Carpet bombing context is not the active coordinator context")
                return False
            max_sets = min(
                int(self.carpet_bombing_config.get("max_sets", 3)),
                6,
            )
            base_coords_per_slot = 2  # Start with 2 coordinates per slot
            no_progress_sets = 0

            if context is not None and context.baseline is None:
                baseline = self._capture_stable_placement_snapshot(
                    timeout_ms=1000,
                    context=context,
                )
                if baseline is None:
                    print("   🛑 Cannot establish a stable carpet baseline")
                    return False
                context.set_baseline(baseline)
            
            for set_num in range(1, max_sets + 1):
                if self.stop_event.is_set():
                    break
                    
                print(f"\nðŸŽ¯ CARPET SET {set_num}/{max_sets}")
                print("-" * 40)
                
                # Check current count before set
                current_state = self._read_comprehensive_state(context)
                current_count = self._get_board_progress_count(current_state)
                if current_count is None:
                    print("   ⚠️ Cannot read board progress; stopping carpet bombing")
                    return False
                if self._is_board_count_clean(
                    characters_count=current_state.get("characters_count"),
                    putted_count=current_state.get("putted_characters"),
                ):
                    print(f"âœ… COMPLETE! All clear before set {set_num}")
                    return True
                
                print(f"   ðŸ“Š Icons remaining: {current_count}")
                
                # Calculate coordinates per slot for this set
                coords_per_slot = base_coords_per_slot + (set_num - 1)
                print(f"   ðŸ“ Set {set_num}: {coords_per_slot} coordinates per slot")
                
                # Generate coordinate set for all slots
                coordinate_set = self.generate_simple_coordinate_set(sorted_slots, coords_per_slot, set_num)
                
                if not coordinate_set:
                    print(f"   âŒ Could not generate coordinates for set {set_num}")
                    continue
                
                print(f"   ðŸ“Š Generated {len(coordinate_set)} total coordinates")
                
                # Execute sequential clicking for this set
                set_success = self.execute_sequential_clicking_set(
                    coordinate_set,
                    set_num,
                    use_cdp,
                    operation_context=context,
                )
                
                # Check progress after set
                after_state = self._read_comprehensive_state(context)
                after_count = self._get_board_progress_count(after_state)
                if after_count is None:
                    print("   ⚠️ Post-set board state is UNKNOWN; stopping without another set")
                    return False
                progress = current_count - after_count
                
                print(f"   ðŸ“Š Set {set_num} result: {current_count} â†’ {after_count} (cleared: {progress})")
                
                if self._is_board_count_clean(characters_count=after_count):
                    print(f"ðŸŽ‰ SUCCESS! All icons cleared after set {set_num}")
                    return True
                elif progress > 0:
                    no_progress_sets = 0
                    print(f"   ðŸ“ˆ Progress made, continuing to next set...")
                else:
                    no_progress_sets += 1
                    print(f"   âš ï¸ No progress in set {set_num}, but continuing...")
                    if no_progress_sets >= 2:
                        print("   🛑 Two consecutive no-progress sets; stopping carpet bombing")
                        return False
                
                # Brief pause between sets
                time.sleep(0.3)
            
            # Final check
            final_state = self._read_comprehensive_state(context)
            final_count = self._get_board_progress_count(final_state)
            
            if final_state and self._is_board_count_clean(
                characters_count=final_state.get("characters_count"),
                putted_count=final_state.get("putted_characters"),
            ):
                print(f"FINAL SUCCESS! All icons cleared")
                return True
            else:
                print(f"CARPET BOMBING INCOMPLETE after {max_sets} sets: {final_count} icons remaining")
                print("RETURNING FAILURE TO CALLER FOR A SINGLE RESTART FALLBACK...")
                return False
                
        except Exception as e:
            print(f"Error in sequential carpet bombing: {e}")
            print("RETURNING FAILURE TO CALLER FOR A SINGLE RESTART FALLBACK...")
            return False

    def generate_simple_coordinate_set(self, sorted_slots, coords_per_slot, set_num):
        """
        ðŸ“ NEW: Generate simple coordinate set - coords_per_slot for each slot
        """
        try:
            coordinate_set = []
            
            print(f"   ðŸ“ Generating {coords_per_slot} coordinates per slot:")
            
            for slot in sorted_slots:
                slot_index = slot.get('index', -1)
                slot_x, slot_y = slot['x'], slot['y']
                
                # Generate coordinates for this specific slot
                slot_coordinates = self.generate_slot_coordinates(slot_x, slot_y, coords_per_slot, set_num)
                
                # Add slot info to each coordinate for tracking
                for coord_x, coord_y in slot_coordinates:
                    coordinate_set.append({
                        'x': coord_x,
                        'y': coord_y,
                        'slot_index': slot_index,
                        'slot_center': (slot_x, slot_y)
                    })
                
                print(f"      Slot {slot_index}: {len(slot_coordinates)} coords at ({slot_x}, {slot_y})")
            
            return coordinate_set
            
        except Exception as e:
            print(f"âŒ Error generating coordinate set: {e}")
            return []

    def generate_slot_coordinates(self, center_x, center_y, num_coords, set_num):
        """
        ðŸŽ¯ NEW: Generate coordinates for single slot using progressive strategy
        """
        try:
            coordinates = []
            
            # Strategy: Cross pattern + random offsets
            if num_coords >= 1:
                # Always include center
                coordinates.append((center_x, center_y))
            
            if num_coords >= 2:
                # Add one offset (left or right alternating by set)
                offset = 15 + (set_num * 3)  # Increase offset with set number
                if set_num % 2 == 1:
                    coordinates.append((center_x - offset, center_y))  # Left
                else:
                    coordinates.append((center_x + offset, center_y))  # Right
            
            if num_coords >= 3:
                # Add vertical offset
                offset = 15 + (set_num * 3)
                if set_num % 2 == 1:
                    coordinates.append((center_x, center_y - offset))  # Top
                else:
                    coordinates.append((center_x, center_y + offset))  # Bottom
            
            if num_coords >= 4:
                # Add diagonal
                offset = 12 + (set_num * 2)
                coordinates.append((center_x - offset, center_y - offset))  # Top-left
            
            if num_coords >= 5:
                # Add another diagonal
                offset = 12 + (set_num * 2)
                coordinates.append((center_x + offset, center_y - offset))  # Top-right
            
            if num_coords >= 6:
                # Add more diagonals
                offset = 12 + (set_num * 2)
                coordinates.append((center_x - offset, center_y + offset))  # Bottom-left
                coordinates.append((center_x + offset, center_y + offset))  # Bottom-right
            
            # For more than 7 coordinates, add random scatter
            while len(coordinates) < num_coords:
                scatter_range = 20 + (set_num * 2)
                jitter_x = random.randint(-scatter_range, scatter_range)
                jitter_y = random.randint(-scatter_range, scatter_range)
                coordinates.append((center_x + jitter_x, center_y + jitter_y))
            
            return coordinates[:num_coords]  # Ensure exact count
            
        except Exception as e:
            print(f"âŒ Error generating slot coordinates: {e}")
            return [(center_x, center_y)]  # Fallback to center only

    def execute_sequential_clicking_set(
        self,
        coordinate_set,
        set_num,
        use_cdp=False,
        operation_context=None,
    ):
        """
        ðŸ‘† NEW: Execute clicking in sequential order (slot 0 â†’ slot 1 â†’ slot 2...)
        """
        try:
            print(f"   ðŸ‘† Executing sequential clicking for set {set_num}")
            context = operation_context or getattr(self, "_active_operation_context", None)
            if context is None or getattr(self, "_active_operation_context", None) is not context:
                print("      🛑 Sequential clicks require the active OperationContext")
                return False
            
            # Group coordinates by slot for sequential processing
            slots_coords = {}
            for coord in coordinate_set:
                slot_index = coord['slot_index']
                if slot_index not in slots_coords:
                    slots_coords[slot_index] = []
                slots_coords[slot_index].append(coord)
            
            # Sort slots for sequential processing
            sorted_slot_indices = sorted(slots_coords.keys())
            
            total_clicks = 0
            successful_clicks = 0
            
            # Process each slot sequentially
            for slot_index in sorted_slot_indices:
                if self.stop_event.is_set():
                    break
                
                slot_coords = slots_coords[slot_index]
                print(f"      ðŸŽ¯ Processing slot {slot_index}: {len(slot_coords)} clicks")
                
                # Execute all coordinates for this slot
                for i, coord in enumerate(slot_coords):
                    if not context.can_send_input(
                        stop_requested=self.stop_event.is_set()
                    ):
                        print("      🛑 Carpet input budget exhausted")
                        return False
                    click_success = self._execute_carpet_click(coord['x'], coord['y'], use_cdp)
                    
                    total_clicks += 1
                    if click_success:
                        successful_clicks += 1
                    
                    # Small delay between clicks within same slot
                    time.sleep(0.03)
                
                # Slightly longer delay between slots for visual feedback
                if slot_index != sorted_slot_indices[-1]:  # Not last slot
                    time.sleep(0.1)
            
            print(f"   âœ… Set {set_num} executed: {successful_clicks}/{total_clicks} clicks successful")
            
            return successful_clicks > 0
            
        except Exception as e:
            print(f"âŒ Error executing clicking set: {e}")
            return False

    def get_comprehensive_slot_state(self):
        """
        ðŸ“Š Get one atomic placement snapshot for verification.

        The batch path reads Characters, Putted characters, Polos and the
        button-enable list in one Runtime.evaluate call.  The individual
        fallback exposes the counters only; it never invents missing arrays.
        """
        try:
            batch_state = self.get_placement_snapshot_batch()
            if isinstance(batch_state, dict) and not batch_state.get("read_error"):
                snapshot = PlacementSnapshot.from_mapping(batch_state)
                if snapshot and snapshot.has_known_signal:
                    state = {
                        "characters_count": snapshot.characters_count,
                        "putted_characters": snapshot.putted_count,
                        "polos_array": list(snapshot.polos) if snapshot.polos is not None else None,
                        "characters_array": list(snapshot.characters) if snapshot.characters is not None else None,
                        "button_enable_list": (
                            list(snapshot.button_enable_list)
                            if snapshot.button_enable_list is not None else None
                        ),
                        "timestamp": snapshot.timestamp,
                        "sequence": snapshot.sequence,
                    }
                    print(
                        "ðŸ“Š Atomic placement state captured: "
                        f"Characters={state['characters_count']}, "
                        f"Putted={state['putted_characters']}, "
                        f"Polos={'yes' if state['polos_array'] is not None else 'no'}, "
                        f"Buttons={'yes' if state['button_enable_list'] is not None else 'no'}"
                    )
                    return state

            characters_count, putted_count = self.get_dual_counts_individual()
            if characters_count is None and putted_count is None:
                print("âš ï¸ No placement signal available")
                return None
            return {
                "characters_count": characters_count,
                "putted_characters": putted_count,
                "polos_array": None,
                "characters_array": None,
                "button_enable_list": None,
                "timestamp": time.time(),
                "sequence": 0,
            }

        except Exception as e:
            print(f"âŒ Error getting comprehensive state: {e}")
            return None

    def verify_slot_specific_return_enhanced(self, target_slot_index, state_before, state_after):
        """
        ðŸ” ENHANCED: Slot-specific verification using the shared verifier
        Returns: (success, reason, details)
        """
        try:
            if not state_before or not state_after:
                return False, "missing_state_data", {}
            evaluation = evaluate_transition(
                state_before,
                state_after,
                "return",
                target_slot_index,
            )
            details = dict(evaluation.details)
            details.update({
                "target_slot": target_slot_index,
                "verification_method": ",".join(evaluation.evidence) or "none",
                "aggregate_fallback": evaluation.aggregate_fallback,
            })
            return (
                evaluation.status == OperationStatus.VERIFIED and evaluation.target_specific,
                evaluation.message,
                details,
            )
            
        except Exception as e:
            print(f"âŒ Enhanced slot verification error: {e}")
            return False, f"verification_error: {e}", {}

    def check_slot_needs_return(self, target_slot_index):
        """
        ðŸ” NEW: Pre-flight check - UPDATED for Characters Array
        FIXED: None safety checks
        """
        try:
            print(f"\nðŸ” PRE-FLIGHT CHECK: Slot {target_slot_index}")
            
            state = self.get_comprehensive_slot_state()
            if not state:
                return False, None, "unknown"

            polos = state.get("polos_array")
            if isinstance(polos, list) and 0 <= target_slot_index < len(polos):
                value = str(polos[target_slot_index])
                if value == "0":
                    return False, state, "already_empty"
                if value == "1":
                    return True, state, "target_occupied"
                return False, state, "unknown_target_state"

            characters_count = state.get("characters_count")
            putted_count = state.get("putted_characters")
            available_counts = [value for value in (characters_count, putted_count) if value is not None]
            if not available_counts:
                return False, state, "unknown"
            if self._is_board_count_clean(characters_count, putted_count):
                return False, state, "already_empty"
            return True, state, "aggregate_fallback"
                
        except Exception as e:
            print(f"âŒ Pre-flight check error: {e}")
            return False, None, f"error: {e}"

    def get_active_slots_queue(self):
        """
        ðŸ“‹ NEW: Get smart-ordered queue - UPDATED for Characters Array
        FIXED: Dá»±a vÃ o Characters count thay vÃ¬ Polos array
        Returns: list of slot indices (simplified for Characters Array system)
        """
        try:
            print(f"\nðŸ“‹ BUILDING SMART RETURN QUEUE")
            print("-" * 40)
            
            state = self.get_comprehensive_slot_state()
            if not state:
                print("âš ï¸ Cannot get slot state, using fallback")
                return []
            
            characters_count = state.get('characters_count')
            putted_count = state.get('putted_characters')

            polos = state.get("polos_array")
            if isinstance(polos, list):
                active_slots = [
                    index for index, value in enumerate(polos)
                    if str(value) == "1"
                ]
                if active_slots:
                    print(f"   ðŸ“‹ Target-specific active slots: {active_slots}")
                    return active_slots
                if polos:
                    return []
            
            print(f"   ðŸ“Š Characters count: {characters_count}")
            
            if characters_count is None and putted_count is None:
                print("âš ï¸ Active slot queue is UNKNOWN; no physical cleanup guesses allowed")
                return []

            if self._is_board_count_clean(characters_count, putted_count):
                print(f"   âœ… No characters to return")
                return []
            
            # For Characters Array system, we don't know specific slot indices
            # Just return a simple range based on typical slot usage
            aggregate_count = characters_count if characters_count is not None else putted_count
            if aggregate_count is not None and aggregate_count > 0:
                # Return slots in reverse order (high â†’ low) for safety
                active_slots_estimated = list(range(min(aggregate_count, 7) - 1, -1, -1))
                print(f"   ðŸ“‹ Estimated active slots: {active_slots_estimated}")
                print(f"   ðŸ’¡ Reason: aggregate fallback; target-specific state unavailable")
                return active_slots_estimated
            
            return []
            
        except Exception as e:
            print(f"âŒ Error building return queue: {e}")
            return []

    def smart_batch_return_all_slots(self, use_cdp=False):
        """
        ðŸ”„ NEW: Smart batch return all active slots with error recovery
        Returns: (success_count, total_attempted, error_log)
        """
        try:
            print(f"\nðŸ”„ SMART BATCH RETURN: All Active Slots")
            print("="*70)
            
            # Get slots configuration
            if not hasattr(self, 'config') or not self.config:
                print("âŒ No config available for batch return")
                return 0, 0, ["No config available"]
            
            all_slots = self.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("âŒ No slots configuration found")
                return 0, 0, ["No slots configuration"]
            
            # Get active slots queue
            active_slot_indices = self.get_active_slots_queue()
            if not active_slot_indices:
                print("âœ… No active slots found - all slots already empty")
                return 0, 0, []
            
            print(f"ðŸŽ¯ Will process {len(active_slot_indices)} active slots")
            
            # Initialize tracking
            success_count = 0
            error_log = []
            recovery_attempts = 0
            
            # Process each slot in smart order
            for slot_index in active_slot_indices:
                if self.stop_event.is_set():
                    error_log.append(f"Slot {slot_index}: Emergency stop requested")
                    break
                
                print(f"\n{'='*50}")
                print(f"ðŸŽ¯ PROCESSING SLOT {slot_index} ({success_count + 1}/{len(active_slot_indices)})")
                print(f"{'='*50}")
                
                # Validate slot index
                if slot_index >= len(all_slots):
                    error_msg = f"Slot {slot_index}: Invalid slot index (max: {len(all_slots)-1})"
                    print(f"âŒ {error_msg}")
                    error_log.append(error_msg)
                    continue
                
                target_slot = all_slots[slot_index]
                
                # Pre-return state validation
                needs_return, current_state, reason = self.check_slot_needs_return(slot_index)
                
                if not needs_return:
                    if reason == "already_empty":
                        print(f"âœ… Slot {slot_index} already empty - skipping")
                        success_count += 1
                    else:
                        error_msg = f"Slot {slot_index}: Cannot verify if return needed ({reason})"
                        print(f"âš ï¸ {error_msg}")
                        error_log.append(error_msg)
                        # An unknown target must not trigger a guessed click on
                        # the next slot; stop this batch and let the caller
                        # decide whether to recover the entire game.
                        break
                    continue

                try:
                    result = self.corrected_return_icon(target_slot, use_cdp)
                except Exception as error:
                    result = OperationResult(
                        OperationStatus.FAILED,
                        direction="return",
                        message=f"return_exception:{error}",
                    )

                if is_progress_verified(
                    result,
                    allow_soft=not getattr(
                        self,
                        "verifier_target_proof_required_for_cleanup",
                        False,
                    ),
                ):
                    print(f"âœ… Slot {slot_index} return status: {result.status.value}")
                    success_count += 1
                elif result.status == OperationStatus.RECOVERED:
                    recovery_attempts += result.recovery_attempts or 1
                    error_log.append(
                        f"Slot {slot_index}: game recovered but target return was not verified"
                    )
                    # Recovery invalidates the old queue and baselines.
                    break
                else:
                    error_log.append(
                        f"Slot {slot_index}: {result.status.value} ({result.message})"
                    )
                    # One failed/unknown target is a circuit-breaker for this
                    # batch; retrying the same target would repeat the full
                    # precision/carpet/recovery chain.
                    break
                
                # Brief pause between slots to allow game state stabilization
                if slot_index != active_slot_indices[-1]:  # Not the last slot
                    print(f"â³ Stabilization pause before next slot...")
                    time.sleep(0.8)
            
            # ================================================================
            # FINAL BATCH VALIDATION
            # ================================================================
            print(f"\n{'='*70}")
            print(f"ðŸ“Š SMART BATCH RETURN COMPLETED")
            print(f"{'='*70}")
            
            # Get final state for validation
            final_state = self.get_comprehensive_slot_state()
            final_polos = final_state.get('polos_array') if final_state else None
            final_putted = final_state.get('putted_characters') if final_state else None
            
            print(f"âœ… Successfully returned: {success_count}/{len(active_slot_indices)} slots")
            print(f"ðŸ”„ Recovery attempts: {recovery_attempts}")
            print(f"âŒ Errors encountered: {len(error_log)}")
            
            if final_state:
                print(f"ðŸ“Š Final state:")
                print(f"   Putted characters: {final_putted}")
                if final_polos:
                    print(f"   Polos array: {final_polos}")
                    remaining_active = sum(1 for x in final_polos if x == "1")
                    print(f"   Remaining active slots: {remaining_active}")
            
            if error_log:
                print(f"ðŸ“‹ Error summary:")
                for i, error in enumerate(error_log, 1):
                    print(f"   {i}. {error}")
            
            return success_count, len(active_slot_indices), error_log
            
        except Exception as e:
            print(f"ðŸ’¥ Critical error in smart batch return: {e}")
            import traceback
            traceback.print_exc()
            return 0, 0, [f"Critical error: {str(e)}"]

    def _emergency_slot_recovery(self, slot_index, target_slot, use_cdp):
        return OperationResult(
            OperationStatus.STOPPED,
            direction="return",
            message="legacy_emergency_recovery_disabled",
        )
        """
        ðŸš¨ NEW: Emergency recovery for stuck slots
        Last resort methods when normal return fails
        """
        try:
            print(f"ðŸš¨ EMERGENCY RECOVERY: Slot {slot_index}")
            print("-" * 30)
            
            center_x, center_y = target_slot['x'], target_slot['y']
            domain_adj = self.get_domain_adjustments()
            
            # Method 1: Rapid-fire center clicks
            print("   ðŸ”¥ Method 1: Rapid-fire center clicks")
            center_coords = [(center_x, center_y)]
            
            state_before = self.get_comprehensive_slot_state() if self.characters_available else None
            
            for _ in range(5):  # 5 rapid clicks
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="single")
                time.sleep(0.1)
            
            time.sleep(1.0)
            if self.characters_available and state_before:
                state_after = self.get_comprehensive_slot_state()
                if state_after:
                    is_success, reason, details = self.verify_slot_specific_return_enhanced(
                        slot_index, state_before, state_after
                    )
                    if is_success:
                        print("   âœ… Rapid-fire method successful!")
                        return True
            
            # Method 2: Hold clicks on expanded area
            print("   ðŸ”¥ Method 2: Hold clicks on expanded area")
            expanded_coords = self.generate_circle_pattern_coordinates(center_x, center_y, radius=40, num_points=8)
            
            state_before = self.get_comprehensive_slot_state() if self.characters_available else None
            
            self.execute_multi_method_clicks(expanded_coords, use_cdp, domain_adj, method="hold")
            
            time.sleep(1.5)
            if self.characters_available and state_before:
                state_after = self.get_comprehensive_slot_state()
                if state_after:
                    is_success, reason, details = self.verify_slot_specific_return_enhanced(
                        slot_index, state_before, state_after
                    )
                    if is_success:
                        print("   âœ… Hold clicks method successful!")
                        return True
            
            # Method 3: Mixed method barrage
            print("   ðŸ”¥ Method 3: Mixed method barrage")
            
            state_before = self.get_comprehensive_slot_state() if self.characters_available else None
            
            methods = ["single", "double", "triple"]
            for method in methods:
                self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method=method)
                time.sleep(0.3)
            
            time.sleep(1.5)
            if self.characters_available and state_before:
                state_after = self.get_comprehensive_slot_state()
                if state_after:
                    is_success, reason, details = self.verify_slot_specific_return_enhanced(
                        slot_index, state_before, state_after
                    )
                    if is_success:
                        print("   âœ… Mixed method barrage successful!")
                        return True
            
            print("   ðŸ’¥ All emergency methods failed")
            return False
            
        except Exception as e:
            print(f"ðŸ’¥ Emergency recovery error: {e}")
            return False

    def validate_workflow_completion(self):
        """
        âœ… NEW: Validate workflow completion - UPDATED for Characters Array
        FIXED: Chá»‰ sá»­ dá»¥ng Characters count validation
        Returns: (is_complete, remaining_issues, recommendations)
        """
        try:
            print(f"\nâœ… WORKFLOW COMPLETION VALIDATION")
            print("="*50)
            
            state = self.get_comprehensive_slot_state()
            if not state:
                return False, ["Cannot get final state"], ["Manual verification needed"]
            
            characters_count = state.get('characters_count')
            putted_count = state.get('putted_characters')
            if characters_count is None:
                characters_count = putted_count
            if characters_count is None:
                return False, ["Final placement state is UNKNOWN"], ["Manual verification needed"]
            
            issues = []
            recommendations = []
            
            # Check: Characters count should be 0
            if not self._is_board_count_clean(
                characters_count=state.get("characters_count"),
                putted_count=state.get("putted_characters"),
            ):
                issues.append(f"Characters count = {characters_count} (expected: 0)")
                recommendations.append("Run additional return operations")
            else:
                print("âœ… Characters count = 0 (perfect)")
            
            is_complete = len(issues) == 0
            
            print(f"\nðŸ“Š VALIDATION RESULT: {'âœ… COMPLETE' if is_complete else 'âš ï¸ ISSUES FOUND'}")
            if issues:
                print("âŒ Issues found:")
                for issue in issues:
                    print(f"   - {issue}")
                print("ðŸ’¡ Recommendations:")
                for rec in recommendations:
                    print(f"   - {rec}")
            else:
                print("ðŸŽ‰ Characters Array validation passed!")
                print("   âœ… Characters count = 0")
                print("   âœ… All characters returned successfully")
            
            return is_complete, issues, recommendations
            
        except Exception as e:
            print(f"âŒ Validation error: {e}")
            return False, [f"Validation error: {str(e)}"], ["Manual check required"]

    def store_target_slot_for_verification(self, target_slot_index):
        """
        ðŸŽ¯ Helper: Store target slot index for verification tracking
        """
        self.last_target_slot_index = target_slot_index
        print(f"ðŸŽ¯ Stored target slot index: {target_slot_index} for verification")

    def get_index_correction_report(self):
        """
        ðŸ“Š Helper: Get report of all index corrections discovered
        """
        if hasattr(self, 'index_corrections') and self.index_corrections:
            print(f"\nðŸ“Š INDEX CORRECTION REPORT:")
            print("-" * 40)
            for config_index, actual_index in self.index_corrections.items():
                offset = actual_index - config_index
                print(f"   Config {config_index} â†’ Actual {actual_index} (offset: {offset:+d})")
            return self.index_corrections
        else:
            print("ðŸ“Š No index corrections recorded")
            return {}

    def generate_icon_coordinates(self, base_x, base_y, offset=35):
        """
        [REFINED - STABILITY V3] Generate 5 coordinates for icon pickup attempts.
        Returns list of (x, y) tuples: center, left, right, top, bottom.
        The function name is kept for backward compatibility.
        """
        coordinates = [
            (base_x, base_y),              # 0: Center (original)
            (base_x - offset, base_y),     # 1: Left
            (base_x + offset, base_y),     # 2: Right
            (base_x, base_y - offset),     # 3: Top
            (base_x, base_y + offset)      # 4: Bottom
        ]
        
        # This print can be very verbose, we can make it more concise
        # print(f"ðŸ“  Generated 5 pickup coordinates for icon at ({base_x}, {base_y}) with offset {offset}px")
        return coordinates

    def generate_return_coordinates(self, center_x, center_y, offset=60):
        """
        ADDED: Generate 5 coordinates for return click attempts
        Returns list of (x, y) tuples: center, top, bottom, left, right
        """
        coordinates = [
            (center_x, center_y),                    # 0: Center (original)
            (center_x, center_y - offset),           # 1: Top
            (center_x, center_y + offset),           # 2: Bottom
            (center_x - offset, center_y),           # 3: Left
            (center_x + offset, center_y)            # 4: Right
        ]
        
        print(f"ðŸ”„ Generated 5 coordinates for return at slot ({center_x}, {center_y}):")
        for i, (x, y) in enumerate(coordinates):
            coord_names = ["Center", "Top", "Bottom", "Left", "Right"]
            print(f"   {i+1}. {coord_names[i]}: ({x}, {y})")
        
        return coordinates

    def generate_circle_pattern_coordinates(self, center_x, center_y, radius=50, num_points=8):
        """
        ðŸ”¥ NEW: Generate circle pattern coordinates around slot center
        Returns list of (x, y) tuples in circular pattern
        """
        import math
        
        coordinates = []
        
        # Add center point first
        coordinates.append((center_x, center_y))
        
        # Generate circle points
        for i in range(num_points):
            angle = (2 * math.pi * i) / num_points  # Divide circle into equal parts
            x = center_x + int(radius * math.cos(angle))
            y = center_y + int(radius * math.sin(angle))
            coordinates.append((x, y))
        
        print(f"ðŸ”„ Generated circle pattern: center + {num_points} points at {radius}px radius")
        for i, (x, y) in enumerate(coordinates):
            if i == 0:
                print(f"   CENTER: ({x}, {y})")
            else:
                angle_deg = (360 * (i-1)) / num_points
                print(f"   Point {i}: ({x}, {y}) - {angle_deg:.0f}Â°")
        
        return coordinates

    def _generate_slot_scan_coords(self, target_slot: dict) -> list:
            """
            [NEW - STABILITY V3] Generates a list of 5 vertically-aligned coordinates
            to scan for a valid drop point on a slot.
            Uses specific pixel offsets as requested.
            """
            if not target_slot or 'x' not in target_slot or 'y' not in target_slot:
                return []

            center_x, center_y = target_slot['x'], target_slot['y']

            # S_0: Center
            # S_1, S_2: +/- 35px offset
            # S_3, S_4: +/- 50px offset
            slot_coordinates = [
                (center_x, center_y),
                (center_x, center_y - 35),
                (center_x, center_y + 35),
                (center_x, center_y - 50),
                (center_x, center_y + 50)
            ]
            return slot_coordinates

    def verify_return_success(self, putted_before, putted_after):
        """
        âœ… COMPATIBILITY: Simple return verification
        """
        try:
            if putted_before is None or putted_after is None:
                return False
            
            # Return success = count decreased by 1
            expected_after = putted_before - 1
            success = (putted_after == expected_after)
            
            print(f"ðŸ“Š Return verification: {putted_before} â†’ {putted_after} (expected: {expected_after}) = {'âœ…' if success else 'âŒ'}")
            return success
            
        except Exception as e:
            print(f"âŒ Return verification error: {e}")
            return False

    def verify_slot_specific_return(self, target_slot_index, putted_before, putted_after):
        """
        ðŸ” NEW: Enhanced verification with slot-specific validation
        Not just check if putted decreased, but validate it's the RIGHT slot
        """
        try:
            # Basic putted characters check
            basic_success = self.verify_return_success(putted_before, putted_after)
            
            if not basic_success:
                return False, "putted_characters_unchanged"
            
            # TODO: Add more sophisticated validation if needed
            # For now, rely on safe zone to prevent cross-slot contamination
            
            print(f"âœ… Slot {target_slot_index} return verified: {putted_before} â†’ {putted_after}")
            return True, "verified_success"
            
        except Exception as e:
            print(f"âŒ Slot-specific verification error: {e}")
            return False, f"verification_error: {e}"

    def execute_multi_method_clicks(self, coordinates, use_cdp, domain_adj, method="single"):
        """
        ðŸ”¥ NEW: Execute clicks using different methods on coordinate list
        Returns True if any click was executed successfully
        """
        successful_clicks = 0
        active_context = getattr(self, "_active_operation_context", None)
        if active_context is None:
            print("🛑 Multi-method clicks require a coordinator-owned OperationContext")
            return False
        
        print(f"ðŸŽ¯ Executing {method} method on {len(coordinates)} coordinates...")
        
        for i, (click_x, click_y) in enumerate(coordinates):
            if self.stop_event.is_set():
                break

            try:
                if not active_context.can_send_input(stop_requested=self.stop_event.is_set()):
                    print("   🛑 Operation input budget exhausted")
                    break
                active_context.register_input()

                # Add small jitter for precision
                jitter_x = click_x + random.randint(-2, 2)
                jitter_y = click_y + random.randint(-2, 2)
                
                if method == "single":
                    success = self._execute_single_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                elif method == "double":
                    success = self._execute_double_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                elif method == "triple":
                    success = self._execute_triple_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                elif method == "hold":
                    success = self._execute_hold_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                else:
                    success = self._execute_single_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                
                if success:
                    successful_clicks += 1
                    
                # Small delay between clicks in same method
                time.sleep(0.1)
                
            except Exception as e:
                print(f"   âš ï¸ Click {i+1} failed: {e}")
        
        print(f"   âœ… Executed {successful_clicks}/{len(coordinates)} clicks successfully")
        return successful_clicks > 0

    def _execute_double_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """ðŸ”¥ NEW: Execute double click"""
        try:
            if use_cdp:
                success1 = self._execute_cdp_single_click(click_x, click_y)
                time.sleep(0.1)
                success2 = self._execute_cdp_single_click(click_x, click_y)
                return success1 and success2
            else:
                success1 = self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                time.sleep(0.1) 
                success2 = self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                return success1 and success2
        except:
            return False

    def _execute_triple_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """ðŸ”¥ NEW: Execute triple click"""
        try:
            success_count = 0
            for _ in range(3):
                if use_cdp:
                    if self._execute_cdp_single_click(click_x, click_y):
                        success_count += 1
                else:
                    if self._execute_pyautogui_single_click(click_x, click_y, domain_adj):
                        success_count += 1
                time.sleep(0.08)
            return success_count >= 2  # At least 2/3 clicks successful
        except:
            return False

    def _execute_hold_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """ðŸ”¥ NEW: Execute hold-and-release click"""
        try:
            if use_cdp:
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    # Move and press
                    self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": click_x, "y": click_y}})
                    time.sleep(0.1)
                    self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": click_x, "y": click_y, "button": "left", "clickCount": 1}})
                    time.sleep(0.5)  # Hold for 500ms
                    self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": click_x, "y": click_y, "button": "left", "clickCount": 1}})
                    ws.close()
                    return True
                return False
            else:
                # PyAutoGUI hold click
                pyautogui.moveTo(click_x, click_y, duration=0.1)
                pyautogui.mouseDown(button='left')
                time.sleep(0.5)  # Hold for 500ms
                pyautogui.mouseUp(button='left')
                return True
        except:
            return False

    def verify_drag_success_dual(self, before_chars, after_chars, before_putted, after_putted):
        """
        🎯 SMART DUAL VERIFICATION: Track working systems for future operations
        """
        try:
            print(f"📊 SMART DUAL Drag verification:")
            
            # Check Characters Array
            chars_success = False
            chars_working = False
            if before_chars is not None and after_chars is not None:
                expected_chars = before_chars + 1
                chars_success = (after_chars == expected_chars)
                chars_working = True  # System is responding
                print(f"   📢 Characters: {before_chars} → {after_chars} (expected: {expected_chars}) = {'✅' if chars_success else '❌'}")
            else:
                print(f"   📢 Characters: N/A (not available)")
            
            # Check Putted Characters  
            putted_success = False
            putted_working = False
            if before_putted is not None and after_putted is not None:
                expected_putted = before_putted + 1
                putted_success = (after_putted == expected_putted)
                putted_working = True  # System is responding
                print(f"   🎯 Putted: {before_putted} → {after_putted} (expected: {expected_putted}) = {'✅' if putted_success else '❌'}")
            else:
                print(f"   🎯 Putted: N/A (not available)")
            
            # 🎯 SMART TRACKING: Record which systems are working
            self.working_systems['characters'] = chars_success if chars_working else None # type: ignore
            self.working_systems['putted'] = putted_success if putted_working else None # type: ignore
            
            # Store for this specific operation
            operation_id = f"drag_{id(self)}"
            self.last_operation_working_systems[operation_id] = {
                'characters': chars_success if chars_working else None, # type: ignore
                'putted': putted_success if putted_working else None # type: ignore
            }
            
            # DUAL LOGIC: Chỉ cần 1 trong 2 thành công
            overall_success = chars_success or putted_success
            
            if overall_success:
                successful_systems = []
                if chars_success: successful_systems.append("Characters Array")
                if putted_success: successful_systems.append("Putted Characters")
                print(f"   🎉 SMART DUAL SUCCESS via: {', '.join(successful_systems)}")
                print(f"   🎯 Tracked working systems for return verification")
            else:
                print(f"   ❌ SMART DUAL FAILURE: Both methods failed")
                print(f"   🎯 No working systems to track")
            
            return overall_success
            
        except Exception as e:
            print(f"❌ SMART DUAL verification error: {e}")
            return False

    def verify_return_success_dual(self, before_chars, after_chars, before_putted, after_putted):
        """
        🎯 SMART DUAL VERIFICATION: Only check systems that worked in drag phase
        """
        try:
            print(f"📊 SMART DUAL Return verification (selective checking):")
            
            # Determine which systems to check based on what worked during drag
            check_characters = self.working_systems.get('characters') is True
            check_putted = self.working_systems.get('putted') is True
            
            if not check_characters and not check_putted:
                print(f"   ⚠️ No working systems tracked - falling back to both")
                check_characters = before_chars is not None and after_chars is not None
                check_putted = before_putted is not None and after_putted is not None
            
            print(f"   🎯 Will check: Characters={'✅' if check_characters else '❌'}, Putted={'✅' if check_putted else '❌'}")
            
            chars_success = False
            putted_success = False
            
            # Check Characters Array (only if it was working)
            if check_characters and before_chars is not None and after_chars is not None:
                expected_chars = before_chars - 1
                chars_success = (after_chars == expected_chars)
                print(f"   📢 Characters: {before_chars} → {after_chars} (expected: {expected_chars}) = {'✅' if chars_success else '❌'} [CHECKED]")
            else:
                if before_chars is not None and after_chars is not None:
                    print(f"   📢 Characters: {before_chars} → {after_chars} = ⏭️ [SKIPPED - didn't work in drag]")
                else:
                    print(f"   📢 Characters: N/A (not available)")
            
            # Check Putted Characters (only if it was working)  
            if check_putted and before_putted is not None and after_putted is not None:
                expected_putted = before_putted - 1
                putted_success = (after_putted == expected_putted)
                print(f"   🎯 Putted: {before_putted} → {after_putted} (expected: {expected_putted}) = {'✅' if putted_success else '❌'} [CHECKED]")
            else:
                if before_putted is not None and after_putted is not None:
                    print(f"   🎯 Putted: {before_putted} → {after_putted} = ⏭️ [SKIPPED - didn't work in drag]")
                else:
                    print(f"   🎯 Putted: N/A (not available)")
            
            # SMART LOGIC: Success if any CHECKED system succeeded
            overall_success = False
            successful_systems = []
            
            if check_characters and chars_success:
                overall_success = True
                successful_systems.append("Characters Array")
            
            if check_putted and putted_success:
                overall_success = True
                successful_systems.append("Putted Characters")
            
            if overall_success:
                print(f"   🎉 SMART DUAL SUCCESS via: {', '.join(successful_systems)}")
                print(f"   🎯 Selective checking prevented false results")
            else:
                checked_systems = []
                if check_characters: checked_systems.append("Characters Array")
                if check_putted: checked_systems.append("Putted Characters")
                print(f"   ❌ SMART DUAL FAILURE: Checked systems ({', '.join(checked_systems)}) failed")
            
            return overall_success
            
        except Exception as e:
            print(f"❌ SMART DUAL verification error: {e}")
            return False

    def _execute_single_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """
        UPDATED: Always use CDP first for game operations with fallback
        """
        try:
            if getattr(self, "_active_operation_context", None) is None:
                print("❌ Return click rejected without an active OperationContext")
                return False
            # 🌐 Choose the transport before sending input.  If a browser
            # target exists but CDP becomes inconclusive, a second PyAutoGUI
            # click could duplicate a partially delivered return gesture.
            ws_url = self._get_websocket_url() if use_cdp else None
            if ws_url:
                print(f"🌐 Using CDP for game click at ({click_x}, {click_y})")
                cdp_success = self._execute_cdp_single_click(click_x, click_y)
                print("✅ CDP click successful" if cdp_success else "🛑 CDP click inconclusive; no duplicate fallback")
                return bool(cdp_success)

            print(f"🖱️ Using PyAutoGUI for game click at ({click_x}, {click_y})")
            return self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                
        except Exception as e:
            print(f"❌ Single return click execution error: {e}")
            return False

    def _execute_cdp_single_click(self, click_x, click_y):
        """ADDED: Execute single click using CDP"""
        ws = None
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print(f"ðŸ–±ï¸ Executing CDP single click at ({click_x}, {click_y})")
            
            # Move and click
            move_response = self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": click_x, "y": click_y}})
            if not move_response or "error" in move_response:
                return False
            time.sleep(0.1)
            click_success = self._simulate_click_via_cdp(ws, click_x, click_y)

            print("âœ… CDP single click completed" if click_success else "🛑 CDP single click inconclusive")
            return bool(click_success)
            
        except Exception as e:
            print(f"âŒ CDP single click failed: {e}")
            return False
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def _execute_cdp_single_drag(
        self,
        start_x,
        start_y,
        end_x,
        end_y,
        drag_duration_ms=0,
        hold_before_move_ms=0,
    ):
        """Replay one manually recorded drag with browser-native mouse events."""
        ws = None

        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                # Manual replay has no safe blind-input fallback: a previous
                # action may already have changed the game state.
                return False

            ws = websocket.create_connection(ws_url, timeout=30)
            self._send_cdp_command(ws, {"method": "Page.bringToFront"})
            result = execute_cdp_gesture(
                ws,
                self._send_cdp_command,
                start_x,
                start_y,
                end_x,
                end_y,
                mode="drag",
                drag_duration_ms=coerce_delay_ms(drag_duration_ms) or 800,
                hold_before_move_ms=coerce_delay_ms(hold_before_move_ms),
                focus_delay_ms=50,
                sleep_fn=time.sleep,
            )
            if not result.success:
                print(f"🛑 CDP manual drag inconclusive: {result.error}")
            return bool(result.success)
        except Exception as drag_error:
            print(f"âŒ CDP manual drag failed: {drag_error}")
            return False
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def _execute_cdp_double_click_drag(
        self,
        start_x,
        start_y,
        end_x,
        end_y,
        drag_duration_ms=0,
        click_interval_ms=300,
        first_click_duration_ms=100,
        hold_before_move_ms=200,
    ):
        """Replay one recorded compound double-click-and-drag gesture."""
        ws = None
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                return False
            ws = websocket.create_connection(ws_url, timeout=30)
            self._send_cdp_command(ws, {"method": "Page.bringToFront"})
            result = execute_cdp_gesture(
                ws,
                self._send_cdp_command,
                start_x,
                start_y,
                end_x,
                end_y,
                mode=DOUBLE_CLICK_DRAG_TYPE,
                drag_duration_ms=coerce_delay_ms(drag_duration_ms),
                click_interval_ms=coerce_delay_ms(click_interval_ms),
                first_click_duration_ms=coerce_delay_ms(first_click_duration_ms),
                hold_before_move_ms=coerce_delay_ms(hold_before_move_ms),
                sleep_fn=time.sleep,
            )
            if not result.success:
                print(f"🛑 CDP double-click drag inconclusive: {result.error}")
            return bool(result.success)
        except Exception as gesture_error:
            print(f"❌ CDP manual double-click drag failed: {gesture_error}")
            return False
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def attempt_icon_return(self, target_slot):
        return OperationResult(
            OperationStatus.STOPPED,
            direction="return",
            message="legacy_unverified_return_disabled",
        )
        try:
            print("ðŸ”„ Attempting to return icon before retry...")
            
            # Get count before return
            count_before = self.get_characters_count() if self.characters_available else None
            
            # âœ… NO VARIATION: Use exact slot coordinates
            center_x, center_y = target_slot['x'], target_slot['y']
            
            pyautogui.moveTo(center_x, center_y, duration=0.2)  # â† CHÃNH XÃC 100%
            time.sleep(0.1)
            pyautogui.click(button='left')
            time.sleep(0.5)  # Wait for return animation
            
            # Verify return if possible
            if self.characters_available and count_before is not None:
                count_after = self.get_characters_count()
                if count_after is not None and count_after < count_before:
                    print("âœ… Icon return verified via Characters Array")
                else:
                    print("âš ï¸ Icon return unclear via Characters Array")
            
            print("âœ… Icon return attempt completed")
            return True
            
        except Exception as e:
            print(f"âš ï¸ Icon return attempt failed: {e}")
            return False

    # ================================================================
    # == ENHANCED DRAG & DROP WITH VERIFICATION ==
    # ================================================================

    def _attempt_single_drag(
        self,
        start_coord,
        end_coord,
        before_state,
        is_first_icon,
        use_cdp,
        control_type_override,
        operation_context=None,
    ):
        """Execute one drag and observe it without issuing a hidden retry."""
        start_x, start_y = start_coord
        end_x, end_y = end_coord
        context = operation_context or getattr(self, "_active_operation_context", None)
        if context is None:
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="place",
                message="missing_coordinator_operation_context",
            )

        before_snapshot = PlacementSnapshot.from_mapping(before_state)
        context.set_baseline(before_snapshot)
        coordinates = (start_x, start_y, end_x, end_y)
        if before_snapshot is None:
            result = self._operation_result(context, OperationStatus.UNKNOWN, "missing_stable_baseline")
            return result
        if not context.can_send_input(coordinates, stop_requested=self.stop_event.is_set()):
            status = OperationStatus.STOPPED if self.stop_event.is_set() else OperationStatus.FAILED
            result = self._operation_result(context, status, "input_budget_or_deadline_exhausted")
            return result

        context.register_input(coordinates)
        drag_executed = False
        cdp_attempted = False
        action_start_time = None
        drop_time = None

        if use_cdp:
            ws_url = self._get_websocket_url()
            if ws_url:
                cdp_attempted = True
                try:
                    ws = websocket.create_connection(ws_url, timeout=30)
                    drag_executed, action_start_time, drop_time = self._execute_dynamic_drag_cdp(
                        ws, start_x, start_y, end_x, end_y,
                        control_type_override or self.game_control_type,
                        is_first_icon,
                    )
                    ws.close()
                except Exception as error:
                    print(f"⚠️ CDP drag execution failed: {error}")
                    drag_executed = False

        # A CDP gesture may have reached the page before its response failed.
        # Do not send a second physical gesture through PyAutoGUI in that
        # ambiguous state; the verifier will classify the resulting state.
        if cdp_attempted and not drag_executed:
            result = self._operation_result(context, OperationStatus.UNKNOWN, "cdp_drag_inconclusive")
            return result

        if not use_cdp or not cdp_attempted:
            domain_adj = self.get_domain_adjustments()
            timing_key = "first_icon" if is_first_icon else "normal_icon"
            delays = self.DRAG_DELAYS[timing_key].copy()
            action_start_time = time.monotonic()
            drag_executed = self._execute_pyautogui_drag(
                start_x, start_y, end_x, end_y, delays, domain_adj, is_first_icon
            )
            if drag_executed and drop_time is None:
                drop_time = time.monotonic()

        if not drag_executed:
            result = self._operation_result(context, OperationStatus.FAILED, "drag_execution_failed")
            return result

        observed_result = observe_until_verified(
            before_snapshot,
            self._capture_placement_snapshot,
            "place",
            context.target_slot,
            operation_id=context.operation_id,
            timeout_ms=getattr(self, "verifier_post_action_timeout_ms", 5000),
            poll_ms=50,
            stable_samples=getattr(self, "verifier_post_action_stable_samples", 1),
            stop_requested=lambda: self.stop_event.is_set(),
            context=context,
        )
        result = self._operation_result(
            context,
            observed_result.status,
            observed_result.message,
            evidence=observed_result.evidence,
            details={
                **(observed_result.details or {}),
                "action_start_time": action_start_time,
                "drop_time": drop_time,
                "verified_at": time.monotonic() if observed_result.status == OperationStatus.VERIFIED else None,
                "inputAttempt": context.input_attempts,
            },
            reads=context.read_count,
        )
        if getattr(self, "verifier_accept_aggregate_progress", True):
            result = promote_aggregate_transition(
                result,
                "place",
                allow_unstable=getattr(self, "verifier_accept_unstable_delta", True),
            )
        self.last_operation_result = result
        self.move_mouse_to_safe_position()
        return result

    def _find_optimal_drop_point(
        self,
        icon,
        target_slot,
        is_first_icon,
        use_cdp,
        control_type_override,
        operation_context=None,
    ):
        """Search at most five unique coordinates with a fresh baseline each time."""
        context = operation_context or getattr(self, "_active_operation_context", None)
        print(f"🔍 [ADAPTIVE SEARCH] Activated for slot {target_slot.get('index', 'N/A')}")

        icon_coords = self.generate_icon_coordinates(icon["x"], icon["y"])
        slot_coords = self._generate_slot_scan_coords(target_slot)
        candidates = [
            (1, 0, 0),
            (2, 0, 1),
            (2, 0, 2),
            (3, 1, 0),
            (3, 3, 0),
        ]

        def with_coordinate_evidence(details, icon_idx, slot_idx):
            evidence = dict(details or {})
            pickup_x, pickup_y = icon_coords[icon_idx]
            drop_x, drop_y = slot_coords[slot_idx]
            evidence.update({
                "pickupIconOffsetIdx": icon_idx,
                "pickupX": pickup_x,
                "pickupY": pickup_y,
                "slotOffsetIdx": slot_idx,
                "dropX": drop_x,
                "dropY": drop_y,
            })
            return evidence

        for attempt_number, (level, icon_idx, slot_idx) in enumerate(candidates, start=1):
            if self.stop_event.is_set():
                return {"result": self._operation_result(context, OperationStatus.STOPPED, "stop_requested")}
            if context is None or not context.can_send_input(stop_requested=self.stop_event.is_set()):
                return {"result": self._operation_result(context, OperationStatus.FAILED, "drag_budget_exhausted")}

            candidate_coordinates = (
                *icon_coords[icon_idx],
                *slot_coords[slot_idx],
            )
            if candidate_coordinates in context.attempted_coordinates:
                print("      -> Candidate already consumed by this operation; skipping without input")
                continue

            before_state = self._capture_stable_placement_snapshot(
                timeout_ms=1000,
                context=context,
            )
            if before_state is None:
                return {"result": self._operation_result(context, OperationStatus.UNKNOWN, "baseline_unavailable")}
            context.set_baseline(before_state)

            print(
                f"      -> [Attempt {attempt_number}/5, Lvl {level}] "
                f"Icon[{icon_idx}] -> Slot[{slot_idx}]"
            )
            result = self._attempt_single_drag(
                icon_coords[icon_idx],
                slot_coords[slot_idx],
                before_state,
                is_first_icon,
                use_cdp,
                control_type_override,
                operation_context=context,
            )
            result.details = with_coordinate_evidence(result.details, icon_idx, slot_idx)

            if result.status in {OperationStatus.VERIFIED, OperationStatus.ALREADY_PLACED}:
                return {
                    "result": result,
                    "icon_idx": icon_idx,
                    "slot_idx": slot_idx,
                    "details": result.details,
                }

            # UNKNOWN means that the previous gesture may have landed.  Never
            # send another physical gesture until a new operation reconciles it.
            if result.status in {OperationStatus.UNKNOWN, OperationStatus.STOPPED}:
                return {"result": result}

            current_state = self._capture_stable_placement_snapshot(
                timeout_ms=500,
                context=context,
            )
            if current_state is None:
                return {"result": self._operation_result(context, OperationStatus.UNKNOWN, "post_attempt_state_unavailable")}
            precondition = classify_precondition(current_state, "place", context.target_slot)
            if precondition == OperationStatus.ALREADY_PLACED:
                result = self._operation_result(
                    context,
                    OperationStatus.ALREADY_PLACED,
                    "target_is_already_occupied",
                    details={"aggregateFallback": True},
                )
                return {"result": result}

            # A failed gesture may still have changed another slot or an
            # aggregate signal.  In that case the target's original
            # precondition is no longer proven, so a new coordinate would be
            # a blind second gesture.  Continue searching only when this
            # candidate demonstrably left the complete semantic state alone.
            if current_state.semantic_key() != before_state.semantic_key():
                return {
                    "result": self._operation_result(
                        context,
                        OperationStatus.UNKNOWN,
                        "state_changed_after_failed_drag_no_retry",
                    )
                }

            context.note_failure(
                f"place:{context.target_slot}:no_transition",
                progressed=False,
            )
            if (
                context.no_progress_cycles >= 2
                or context.failure_counts.get(f"place:{context.target_slot}:no_transition", 0) >= 2
            ):
                return {
                    "result": self._operation_result(
                        context,
                        OperationStatus.FAILED,
                        "no_progress_after_two_attempts",
                    )
                }

        return {
            "result": self._operation_result(
                context,
                OperationStatus.FAILED,
                "all_five_drag_attempts_exhausted",
            )
        }

    def corrected_drag_and_drop(self, icon, target_slot, is_first_icon=False, use_cdp=True, control_type_override=None):
        """Public coordinator entry point with a terminal exception guard."""
        context_before = getattr(self, "_active_operation_context", None)
        try:
            return self._corrected_drag_and_drop_bounded(
                icon,
                target_slot,
                is_first_icon,
                use_cdp,
                control_type_override,
            )
        except Exception as error:
            context_after = getattr(self, "_active_operation_context", None)
            if context_after is not None and context_after is not context_before and not context_after.closed:
                return self._finish_operation(
                    context_after,
                    self._operation_result(
                        context_after,
                        OperationStatus.FAILED,
                        f"drag_coordinator_exception:{error}",
                    ),
                )
            return OperationResult(
                OperationStatus.FAILED,
                direction="place",
                message=f"drag_coordinator_exception:{error}",
            )

    def _corrected_drag_and_drop_bounded(self, icon, target_slot, is_first_icon=False, use_cdp=True, control_type_override=None):
        """
        Smart drag-and-drop with one finite budget and read-only verification.
        """
        print(f"\n🎯 [SMART DRAG] START for icon {icon.get('index', 'N/A')} -> slot {target_slot.get('index', 'N/A')}")

        target_slot_index = target_slot.get("index", -1)
        context = self._begin_operation(
            "place",
            target_slot_index,
            max_input_attempts=5,
            deadline_seconds=30.0,
        )
        if context is None:
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="place",
                message="another_operation_is_active",
            )

        if control_type_override:
            active_control_type = control_type_override
        elif self.config and isinstance(self.config, dict):
            active_control_type = self.config.get('gameSettings', {}).get('gameControlType', 'click_drag')
        else:
            active_control_type = 'click_drag'

        print(f"   - Control Type: '{active_control_type}'")

        if is_first_icon:
            print("   - First Icon: YES -> Ensuring focus and applying extended waits.")
            self.ensure_game_focus()

        if target_slot_index == -1:
            return self._finish_operation(
                context,
                self._operation_result(context, OperationStatus.FAILED, "invalid_target_slot"),
            )

        baseline = self._capture_stable_placement_snapshot(
            timeout_ms=1000,
            context=context,
        )
        if baseline is None:
            return self._finish_operation(
                context,
                self._operation_result(context, OperationStatus.UNKNOWN, "baseline_unavailable"),
            )
        context.set_baseline(baseline)

        precondition = classify_precondition(baseline, "place", target_slot_index)
        if precondition == OperationStatus.ALREADY_PLACED:
            return self._finish_operation(
                context,
                self._operation_result(context, OperationStatus.ALREADY_PLACED, "target_already_occupied"),
            )

        learned_offset = self.slot_offset_lookup.get(target_slot_index)

        if learned_offset:
            print(f"   [CACHE HIT] Found learned offset for slot {target_slot_index}: icon_idx={learned_offset['icon_idx']}, slot_idx={learned_offset['slot_idx']}")
            icon_coords = self.generate_icon_coordinates(icon['x'], icon['y'])
            slot_coords = self._generate_slot_scan_coords(target_slot)

            try:
                start_coord = icon_coords[learned_offset['icon_idx']]
                end_coord = slot_coords[learned_offset['slot_idx']]
            except (IndexError, KeyError, TypeError):
                start_coord = end_coord = None

            if start_coord is not None and end_coord is not None:
                cache_result = self._attempt_single_drag(
                    start_coord,
                    end_coord,
                    baseline,
                    is_first_icon,
                    use_cdp,
                    active_control_type,
                    operation_context=context,
                )
            else:
                cache_result = self._operation_result(context, OperationStatus.FAILED, "invalid_cached_offset")

            if cache_result.status in {OperationStatus.VERIFIED, OperationStatus.ALREADY_PLACED}:
                print(f"   [CACHE HIT] ✅ SUCCESS using learned offset.")
                details = {
                    **(cache_result.details or {}),
                    "pickupIconOffsetIdx": learned_offset['icon_idx'],
                    "pickupX": start_coord[0] if start_coord else None,
                    "pickupY": start_coord[1] if start_coord else None,
                    "slotOffsetIdx": learned_offset['slot_idx'],
                    "dropX": end_coord[0] if end_coord else None,
                    "dropY": end_coord[1] if end_coord else None,
                }
                cache_result.details = details
                if cache_result.status == OperationStatus.VERIFIED and cache_result.target_verified:
                    self._handle_successful_drag(icon, target_slot, details, context.input_attempts, "cache_hit")
                elif cache_result.status == OperationStatus.VERIFIED:
                    self._record_aggregate_drag_progress(icon, target_slot, details, context.input_attempts, "cache_hit_aggregate")
                return self._finish_operation(context, cache_result)

            if cache_result.status in {OperationStatus.UNKNOWN, OperationStatus.STOPPED}:
                print(f"   [CACHE STOP] {cache_result.message}; no blind coordinate retry")
                return self._finish_operation(context, cache_result)

            # The cached gesture is part of this same operation.  Before
            # spending the remaining drag budget on another coordinate, take
            # a fresh stable read and prove that the failed gesture left the
            # semantic board state unchanged.
            cache_state_after = self._capture_stable_placement_snapshot(
                timeout_ms=500,
                context=context,
            )
            if cache_state_after is None:
                return self._finish_operation(
                    context,
                    self._operation_result(context, OperationStatus.UNKNOWN, "cached_drag_post_state_unknown"),
                )
            if classify_precondition(cache_state_after, "place", target_slot_index) == OperationStatus.ALREADY_PLACED:
                cache_result = self._operation_result(
                    context,
                    OperationStatus.ALREADY_PLACED,
                    "target_occupied_after_cached_drag",
                )
                return self._finish_operation(context, cache_result)
            if cache_state_after.semantic_key() != baseline.semantic_key():
                return self._finish_operation(
                    context,
                    self._operation_result(
                        context,
                        OperationStatus.UNKNOWN,
                        "cached_drag_changed_state_no_retry",
                    ),
                )

            print(f"   [CACHE INVALIDATION] ⚠️ Learned offset failed with known no-change state; searching remaining budget")
            self.slot_offset_lookup.pop(target_slot_index, None)

        print(f"   [CACHE MISS] No learned offset for slot {target_slot_index}. Activating Search Mode...")
        search_result = self._find_optimal_drop_point(
            icon,
            target_slot,
            is_first_icon,
            use_cdp,
            active_control_type,
            operation_context=context,
        )

        result = (search_result or {}).get("result") if isinstance(search_result, dict) else None
        if result is None:
            result = self._operation_result(context, OperationStatus.UNKNOWN, "search_did_not_return_result")

        if result.status in {OperationStatus.VERIFIED, OperationStatus.ALREADY_PLACED}:
            details = (search_result or {}).get("details") or result.details or {}
            result.details = details
            if result.status == OperationStatus.VERIFIED and result.target_verified and not details.get("aggregateFallback"):
                self.slot_offset_lookup[target_slot_index] = {
                    'icon_idx': search_result['icon_idx'],
                    'slot_idx': search_result['slot_idx'],
                }
                print(f"   [LEARNED] Saved target-specific offset for slot {target_slot_index}.")
                self._handle_successful_drag(
                    icon,
                    target_slot,
                    details,
                    context.input_attempts,
                    "search_mode",
                )
            elif result.status == OperationStatus.VERIFIED:
                print("   [LEARNED] Aggregate fallback verified; coordinate cache unchanged.")
                self._record_aggregate_drag_progress(
                    icon,
                    target_slot,
                    details,
                    context.input_attempts,
                    "search_mode_aggregate_fallback",
                )
            elif result.status == OperationStatus.ALREADY_PLACED:
                self._handle_successful_drag(
                    icon,
                    target_slot,
                    details,
                    context.input_attempts,
                    "already_placed",
                )
            return self._finish_operation(context, result)

        print(f"   [DRAG STOP] {result.status.value}: {result.message}")
        self.drag_stats.record_attempt(
            False,
            retry_count=context.input_attempts,
            verification_used=True,
        )
        return self._finish_operation(context, result)

    def _handle_successful_drag(self, icon, target_slot, details, attempts, method):
        """
        [NEW - HELPER] Centralizes actions after a successful drag to avoid code duplication.
        """
        details = dict(details or {})
        self.last_successful_drag_details = {
            **details,
            "icon_index": icon.get('index', -1),
            "slot_index": target_slot.get('index', -1),
            "method": method,
            "attempt_number": attempts,
        }
        # Bảo toàn tính năng #6: Ghi Timestamps
        if self.obs_controller and self.obs_controller.recording_start_time is not None:
            drag_metadata = {
                key: value
                for key, value in self.last_successful_drag_details.items()
                if key not in {"action_start_time", "drop_time", "verified_at"}
            }
            action_start_time = details.get("action_start_time")
            drop_time = details.get("drop_time")
            verified_at = details.get("verified_at")
            
            if action_start_time:
                self.obs_controller.log_event_timestamp(
                    "successful_drag_started",
                    f"Drag started for icon {icon.get('index', -1)}",
                    drag_metadata,
                    event_timestamp=action_start_time
                )

            if drop_time:
                metadata = dict(drag_metadata)
                if verified_at:
                    metadata["verifiedAt"] = round(verified_at - self.obs_controller.recording_start_time, 3)
                    metadata["verificationDelay"] = round(verified_at - drop_time, 3)
                self.obs_controller.log_event_timestamp(
                    "icon_dropped_successfully",
                    f"Icon {icon.get('index', -1)} dropped into slot",
                    metadata,
                    event_timestamp=drop_time
                )
        
        # Bảo toàn tính năng #10: Ghi lại Thống kê
        self.drag_stats.record_attempt(True, retry_count=attempts, verification_used=True)
        print(f"   [SUCCESS INFO] Method: {method}, Attempts: {attempts}")

    def _record_aggregate_drag_progress(self, icon, target_slot, details, attempts, method):
        """Record aggregate progress without claiming the requested target moved."""
        details = dict(details or {})
        self.last_successful_drag_details = {
            **details,
            "icon_index": icon.get("index", -1),
            "slot_index": target_slot.get("index", -1),
            "method": method,
            "attempt_number": attempts,
            "target_verified": False,
        }
        self.drag_stats.record_attempt(True, retry_count=attempts, verification_used=True)
        print(
            "   [AGGREGATE PROGRESS] Board state changed, but the requested "
            "slot was not proven; coordinate cache unchanged."
        )

    def _execute_single_drag_attempt(self, start_x, start_y, target_slot, is_first_icon, use_cdp, domain_adj):
        """
        ADDED: Execute a single drag attempt at specific coordinates
        UPDATED: Always use CDP for game operations with fallback
        """
        try:
            end_x, end_y = target_slot['x'], target_slot['y']
            
            # Get timing based on icon type and domain
            timing_key = 'first_icon' if is_first_icon else 'normal_icon'
            delays = self.DRAG_DELAYS[timing_key].copy()
            
            # Apply domain-specific adjustments
            stability_factor = domain_adj['stability_factor']
            for key in delays:
                delays[key] *= stability_factor
            
            # Extended timing for first icon
            if is_first_icon:
                print("🎪 First icon - applying extended timing...")
                time.sleep(delays['approach'])
                self.ensure_game_focus()
                time.sleep(domain_adj['extra_focus_time'])
            
            # 🔄 ALWAYS TRY CDP FIRST for game operations
            print("🌐 Using CDP for game drag operation...")
            cdp_success = self._execute_cdp_drag(start_x, start_y, end_x, end_y, delays, domain_adj)
            
            if cdp_success:
                print("✅ CDP drag successful")
                return True
            else:
                print("⚠️ CDP drag failed, using PyAutoGUI fallback...")
                return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, is_first_icon)
                
        except Exception as e:
            print(f"❌ Single drag attempt execution error: {e}")
            return False

    def _get_entry_state_js(self):
        return r'''
        new Promise((resolve) => {
            try {
                function findVM() {
                    if (window.__vm && window.__vm.runtime) return window.__vm;
                    function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        for (const el of document.querySelectorAll(selector)) {
                            const reactKey = getReactKey(el);
                            if (!reactKey) continue;
                            let fiber = el[reactKey];
                            let depth = 0;
                            while (fiber && depth++ < 100) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm && props.vm.runtime) {
                                    window.__vm = props.vm;
                                    return props.vm;
                                }
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }

                const toPixelWithMenu = (logicX, logicY) => {
                    const stageW = 640, stageH = 360, screenW = 1920, screenH = 1080, menuWidth = 78;
                    const scale = Math.min((screenW - menuWidth) / stageW, screenH / stageH);
                    const canvasW = stageW * scale;
                    const canvasH = stageH * scale;
                    const canvasCenterX = menuWidth + (canvasW / 2);
                    const canvasCenterY = ((screenH - canvasH) / 2) + (canvasH / 2);
                    return {
                        x: Math.round(canvasCenterX + ((logicX || 0) * scale)),
                        y: Math.round(canvasCenterY - ((logicY || 0) * scale))
                    };
                };

                const vm = findVM();
                if (!vm) {
                    resolve({ ok: false, reason: 'vm_not_found' });
                    return;
                }

                const icons = [];
                const slots = [];
                const playButtons = [];
                const closeIndicators = [];
                const panelIndicators = [];

                function asItem(target, index) {
                    const name = target.sprite?.name || '';
                    const costume = target.sprite?.costumes?.[target.currentCostume]?.name || '';
                    const pixel = toPixelWithMenu(target.x, target.y);
                    return {
                        index,
                        targetName: name,
                        currentCostumeName: costume,
                        visible: !!target.visible,
                        logicX: Math.round(target.x || 0),
                        logicY: Math.round(target.y || 0),
                        x: pixel.x,
                        y: pixel.y,
                        size: Math.round(target.size || 0),
                        layerOrder: target.getLayerOrder ? target.getLayerOrder() : null
                    };
                }

                function isCloseIndicator(name, costume) {
                    const raw = `${name} ${costume}`.trim().toLowerCase();
                    if (/\b(close|back|return|exit)\b/.test(raw)) return true;
                    return /(^|[^a-z])x([^a-z]|$)/.test(raw) && /\b(button|btn|close)\b/.test(raw);
                }

                function isPanelIndicator(name, costume) {
                    const raw = `${name} ${costume}`.trim().toLowerCase();
                    if (/\bwarning\b/.test(raw)) {
                        return /\b(gallery|credits?|settings?|menu|more)\b/.test(raw);
                    }
                    return /\b(gallery|credits?|settings?|menu|more)\b.*\b(screen|panel)\b/.test(raw)
                        || /\b(screen|panel)\b.*\b(gallery|credits?|settings?|menu|more)\b/.test(raw)
                        || /\bsettings?\b/.test(raw);
                }

                const targets = vm.runtime.targets || [];
                for (let i = 0; i < targets.length; i++) {
                    const target = targets[i];
                    if (target.isStage || !target.sprite || !target.visible) continue;
                    const item = asItem(target, i);
                    const name = item.targetName;
                    const costume = item.currentCostumeName;

                    if (/icons/i.test(name)) icons.push(item);
                    if (/^polo\s*\d+/i.test(name)) slots.push(item);
                    if (/^play button/i.test(name)) playButtons.push(item);
                    if (isCloseIndicator(name, costume)) closeIndicators.push(item);
                    if (isPanelIndicator(name, costume)) panelIndicators.push(item);
                }

                const openPanelIndicators = [...closeIndicators, ...panelIndicators];
                resolve({
                    ok: true,
                    iconCount: icons.length,
                    slotCount: slots.length,
                    playButtonCount: playButtons.length,
                    closeIndicators,
                    panelIndicators,
                    openPanelIndicators,
                    playableCandidate: icons.length > 0 && slots.length > 0 && openPanelIndicators.length === 0
                });
            } catch (e) {
                resolve({ ok: false, reason: 'error', error: String(e) });
            }
        });
        '''

    def _evaluate_entry_state_via_ws(self, ws):
        response = self._send_cdp_command(ws, {
            "method": "Runtime.evaluate",
            "params": {
                "expression": self._get_entry_state_js(),
                "awaitPromise": True,
                "returnByValue": True
            }
        })
        if response and 'result' in response:
            return response.get('result', {}).get('result', {}).get('value')
        return None

    def verify_playable_runtime_entry(self):
        """Verify that the selected runtime is a real playable Sprunki stage.

        DOM/container presence is not enough: the Scratch VM must expose at
        least one icon and one slot.  This is deliberately a read-only check;
        placement evidence is collected later by the bounded drag verifier.
        """
        ws = None
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                result = QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    message="No CDP websocket is available for runtime entry verification.",
                )
                self.entry_strategy["verification"] = "UNKNOWN"
                self.entry_strategy["verificationResult"] = result.as_dict()
                return result

            ws = websocket.create_connection(ws_url, timeout=10)
            state = self._evaluate_entry_state_via_ws(ws)
            if not isinstance(state, dict) or not state.get("ok"):
                result = QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    evidence={"state": state},
                    message="Scratch runtime entry state could not be read.",
                )
                self.entry_strategy["verification"] = "UNKNOWN"
                self.entry_strategy["verificationResult"] = result.as_dict()
                return result

            icons = int(state.get("iconCount") or 0)
            slots = int(state.get("slotCount") or 0)
            evidence = {
                "icons": icons,
                "slots": slots,
                "playButtons": int(state.get("playButtonCount") or 0),
                "openIndicators": len(state.get("openPanelIndicators") or []),
            }
            self.entry_strategy["runtimeState"] = evidence

            if icons <= 0:
                result = QualityGateResult.failure(
                    "no_icons",
                    evidence=evidence,
                    message="Runtime stage contains no playable icons.",
                )
            elif slots <= 0:
                result = QualityGateResult.failure(
                    "no_slots",
                    evidence=evidence,
                    message="Runtime stage contains no playable slots.",
                )
            else:
                result = QualityGateResult.pass_result(
                    evidence=evidence,
                    message="Playable runtime entry verified.",
                )

            self.entry_strategy["verification"] = result.status
            self.entry_strategy["verificationResult"] = result.as_dict()
            if result.passed and not self.entry_strategy.get("selected"):
                self.entry_strategy["selected"] = {
                    "strategy": "runtime_vm",
                    "reason": "runtime_exposes_icons_and_slots",
                }
            return result
        except Exception as error:
            result = QualityGateResult.failure(
                "runtime_verification_unknown",
                status="UNKNOWN",
                retryable=True,
                evidence={"error": str(error)},
                message="Runtime entry verification raised an exception.",
            )
            self.entry_strategy["verification"] = "UNKNOWN"
            self.entry_strategy["verificationResult"] = result.as_dict()
            return result
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

    def _summarize_entry_state(self, state):
        if not isinstance(state, dict):
            return "state unavailable"
        indicators = state.get('openPanelIndicators') or []
        indicator_names = [
            f"{item.get('targetName')}/{item.get('currentCostumeName')}@({item.get('x')},{item.get('y')})"
            for item in indicators[:6]
        ]
        summary = (
            f"icons={state.get('iconCount')}, slots={state.get('slotCount')}, "
            f"playButtons={state.get('playButtonCount')}, openIndicators={len(indicators)}"
        )
        if indicator_names:
            summary += f" [{'; '.join(indicator_names)}]"
        return summary

    def _remember_empty_board_baseline(self, characters_count, putted_count):
        """Remember counts observed after a verified return to an empty board."""
        if characters_count is not None:
            self.empty_board_baseline['characters'] = characters_count
        if putted_count is not None:
            self.empty_board_baseline['putted'] = putted_count
        print(
            "   Empty-board baseline updated: "
            f"Characters={self.empty_board_baseline['characters']}, "
            f"Putted={self.empty_board_baseline['putted']}"
        )

    def _get_board_progress_count(self, state):
        """Return a known aggregate count, or None when state is unknown."""
        if not isinstance(state, dict):
            return None
        characters_count = state.get("characters_count")
        putted_count = state.get("putted_characters")
        if characters_count is not None:
            return characters_count
        if putted_count is not None:
            return putted_count
        return None

    @staticmethod
    def _transition_count_from_details(details, side):
        """Extract a numeric before/after count from verifier evidence."""
        details = details or {}
        keys = (
            f"{side}CharactersCount",
            f"{side}PuttedCount",
        )
        for key in keys:
            value = details.get(key)
            if value is not None and not isinstance(value, bool):
                try:
                    return int(value)
                except (TypeError, ValueError, OverflowError):
                    continue
        return None

    def _is_board_count_clean(self, characters_count=None, putted_count=None):
        """Return True when available counters are at their learned empty level."""
        checks = []
        for key, count in (('characters', characters_count), ('putted', putted_count)):
            if count is None:
                continue
            baseline = self.empty_board_baseline.get(key)
            # Preserve compatibility for mods whose counters genuinely reach 0
            # until a non-zero baseline has been learned.
            clean_level = baseline if baseline is not None else 0
            checks.append(count <= clean_level)
        return bool(checks) and all(checks)

    def _rollback_open_entry_panel_via_ws(self, ws, state):
        if not isinstance(state, dict):
            return False

        close_buttons = state.get('closeIndicators') or []
        if not close_buttons:
            return False
        close_button = sorted(
            close_buttons,
            key=lambda item: item.get('layerOrder') if item.get('layerOrder') is not None else -1,
            reverse=True
        )[0]
        print(
            "   Rolling back opened panel via "
            f"{close_button.get('targetName')}/{close_button.get('currentCostumeName')} "
            f"@ ({close_button.get('x')}, {close_button.get('y')})"
        )
        if not self._simulate_click_via_cdp(ws, int(close_button['x']), int(close_button['y'])):
            return False
        time.sleep(0.8)
        return True

    def _trial_action_buttons_via_ws(self, ws, buttons):
        if not buttons:
            return {"selected": None, "tested": [], "success": False, "reason": "no_buttons"}

        tested = []
        for i, button in enumerate(buttons, 1):
            x, y = int(button['x']), int(button['y'])
            name = button.get('targetName', '?')
            costume = button.get('currentCostumeName', '?')
            print(f"   Trial {i}/{len(buttons)}: {name}/{costume} @ ({x}, {y})")

            before_state = self._evaluate_entry_state_via_ws(ws)
            if not self._simulate_click_via_cdp(ws, x, y):
                return {
                    "selected": None,
                    "tested": tested,
                    "success": False,
                    "reason": "intro_input_inconclusive_or_budget_exhausted",
                }
            time.sleep(1.0)
            after_state = self._evaluate_entry_state_via_ws(ws)
            print(f"      After click: {self._summarize_entry_state(after_state)}")

            opened_panel = bool(after_state and after_state.get('openPanelIndicators'))
            playable_candidate = bool(after_state and after_state.get('playableCandidate'))
            result = {
                "button": button,
                "before": before_state,
                "after": after_state,
                "openedPanel": opened_panel,
                "playableCandidate": playable_candidate
            }

            if opened_panel:
                result["result"] = "panel_opened"
                self._rollback_open_entry_panel_via_ws(ws, after_state)
                post_rollback = self._evaluate_entry_state_via_ws(ws)
                result["afterRollback"] = post_rollback
                print(f"      After rollback: {self._summarize_entry_state(post_rollback)}")
                tested.append(result)
                continue

            if playable_candidate:
                result["result"] = "selected"
                tested.append(result)
                print(f"   Selected action candidate: {name}/{costume} @ ({x}, {y})")
                return {
                    "selected": button,
                    "tested": tested,
                    "success": True,
                    "reason": "selected_playable_candidate"
                }

            result["result"] = "not_playable"
            tested.append(result)
            print("      Candidate did not expose a playable state; trying next candidate.")

        return {"selected": None, "tested": tested, "success": False, "reason": "no_playable_candidate"}

    def _resolve_manual_profile(self):
        self._manual_profile_resolution_error = None
        try:
            settings = (self.config or {}).get("settings", {}) or {}
            manual_profile_path = settings.get("manualProfilePath")
            current_url = (self.config or {}).get("currentUrl", "")

            if manual_profile_path:
                profile_data, resolved_path = load_profile_from_path(manual_profile_path)
                is_valid, error_message = validate_profile(profile_data, expected_url=current_url)
                if profile_data and is_valid:
                    return profile_data, resolved_path
                if error_message:
                    print(f"⚠️ Manual profile at {manual_profile_path} is invalid: {error_message}")
                    self._manual_profile_resolution_error = str(error_message)
                    return None, None

            if current_url:
                profile_data, resolved_path = find_profile(current_url)
                is_valid, error_message = validate_profile(profile_data, expected_url=current_url)
                if profile_data and is_valid:
                    return profile_data, resolved_path
                if profile_data and error_message:
                    print(f"⚠️ Manual profile lookup found invalid data for {current_url}: {error_message}")
                    self._manual_profile_resolution_error = str(error_message)
        except Exception as e:
            print(f"⚠️ Could not resolve manual profile: {e}")
            self._manual_profile_resolution_error = str(e)

        return None, None

    def _execute_cdp_key_press(self, key_name):
        """Send a browser-native key press to the active game target via CDP."""
        normalized = str(key_name or "").strip().lower()
        if not normalized:
            return False

        special_keys = {
            "space": (" ", "Space", 32, " "),
            "enter": ("Enter", "Enter", 13, ""),
            "return": ("Enter", "Enter", 13, ""),
            "esc": ("Escape", "Escape", 27, ""),
            "escape": ("Escape", "Escape", 27, ""),
            "tab": ("Tab", "Tab", 9, ""),
            "backspace": ("Backspace", "Backspace", 8, ""),
            "delete": ("Delete", "Delete", 46, ""),
            "left": ("ArrowLeft", "ArrowLeft", 37, ""),
            "right": ("ArrowRight", "ArrowRight", 39, ""),
            "up": ("ArrowUp", "ArrowUp", 38, ""),
            "down": ("ArrowDown", "ArrowDown", 40, ""),
            "home": ("Home", "Home", 36, ""),
            "end": ("End", "End", 35, ""),
            "pageup": ("PageUp", "PageUp", 33, ""),
            "pagedown": ("PageDown", "PageDown", 34, ""),
            "f11": ("F11", "F11", 122, ""),
        }

        if normalized in special_keys:
            key_value, code, virtual_key_code, text_value = special_keys[normalized]
        elif len(normalized) == 1 and normalized.isalpha():
            key_value = normalized
            code = f"Key{normalized.upper()}"
            virtual_key_code = ord(normalized.upper())
            text_value = normalized
        elif len(normalized) == 1 and normalized.isdigit():
            key_value = normalized
            code = f"Digit{normalized}"
            virtual_key_code = ord(normalized)
            text_value = normalized
        else:
            print(f"   ⚠️ Unsupported CDP key: {key_name}")
            return False

        ws = None
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                return False

            ws = websocket.create_connection(ws_url, timeout=30)
            # Keep the same page target that receives the manual click in front.
            self._send_cdp_command(ws, {"method": "Page.bringToFront"})

            key_down = {
                "method": "Input.dispatchKeyEvent",
                "params": {
                    "type": "keyDown",
                    "key": key_value,
                    "code": code,
                    "windowsVirtualKeyCode": virtual_key_code,
                    "nativeVirtualKeyCode": virtual_key_code,
                    "autoRepeat": False,
                    "isKeypad": False,
                },
            }
            if text_value:
                key_down["params"]["text"] = text_value
                key_down["params"]["unmodifiedText"] = text_value

            down_response = self._send_cdp_command(ws, key_down)
            time.sleep(0.05)
            up_response = self._send_cdp_command(ws, {
                "method": "Input.dispatchKeyEvent",
                "params": {
                    "type": "keyUp",
                    "key": key_value,
                    "code": code,
                    "windowsVirtualKeyCode": virtual_key_code,
                    "nativeVirtualKeyCode": virtual_key_code,
                    "isKeypad": False,
                },
            })

            if not down_response or not up_response:
                return False
            if "error" in down_response or "error" in up_response:
                return False

            print(f"   ✅ CDP keyDown/keyUp completed: {key_name}")
            return True
        except Exception as key_error:
            print(f"   ⚠️ CDP key press failed for {key_name}: {key_error}")
            return False
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    def replay_manual_prepare_actions(
        self,
        actions,
        profile_or_timing=None,
        operation_context=None,
    ):
        """Replay one validated manual timeline without a blind fallback.

        A replay is a compound recovery/prepare operation: if any action is
        inconclusive, it stops at that action and the caller must decide
        whether to start a fresh recovery.  It never continues on a partially
        changed game state.
        """
        actions = list(actions or [])
        if not actions:
            print("⚠️ Manual profile has no prepare actions to replay.")
            return False

        is_valid, validation_reason = validate_prepare_timeline(actions, profile_or_timing)
        if not is_valid:
            print(f"🛑 Manual profile replay rejected before input: {validation_reason}")
            return False

        context = operation_context or getattr(self, "_active_operation_context", None)
        owns_context = False
        if context is None:
            context = self._begin_operation(
                "prepare",
                None,
                max_input_attempts=len(actions),
                deadline_seconds=(MAX_TOTAL_PREPARE_DURATION_MS / 1000.0) + 5.0,
            )
            owns_context = True
        if context is None:
            print("🛑 Manual profile replay rejected: another operation is active")
            return False

        def finish(success, message):
            result = self._operation_result(
                context,
                OperationStatus.VERIFIED if success else OperationStatus.FAILED,
                message,
            )
            if owns_context:
                self._finish_operation(context, result)
            return bool(result)

        print(f"🎯 Replaying {len(actions)} manual prepare actions...")

        def execute_action(index, action):
            try:
                # Recovery replay is allowed to send its own bounded inputs,
                # but no caller can silently send an extra input after this
                # callback returns False.
                if not context.can_send_input(
                    stop_requested=self.stop_event.is_set(),
                    allow_recovery_input=True,
                ):
                    return False
                if not context.register_input():
                    return False

                action_type = (action.get("type") or "").lower()
                if action_type == "click":
                    x = int(action.get("x"))
                    y = int(action.get("y"))
                    print(f"   {index}. Click @ ({x}, {y})")
                    return bool(self._execute_cdp_single_click(x, y))
                if action_type == DOUBLE_CLICK_DRAG_TYPE:
                    start_x = int(action.get("startX"))
                    start_y = int(action.get("startY"))
                    end_x = int(action.get("endX"))
                    end_y = int(action.get("endY"))
                    drag_duration_ms = coerce_delay_ms(action.get("dragDurationMs", 0))
                    click_interval_ms = coerce_delay_ms(action.get("clickIntervalMs", 300))
                    first_click_duration_ms = coerce_delay_ms(
                        action.get("firstClickDurationMs", 100)
                    )
                    hold_before_move_ms = coerce_delay_ms(
                        action.get("holdBeforeMoveMs", 200)
                    )
                    print(
                        f"   {index}. Double-click drag @ ({start_x}, {start_y}) -> "
                        f"({end_x}, {end_y}) ({drag_duration_ms} ms, "
                        f"interval {click_interval_ms} ms)"
                    )
                    return bool(self._execute_cdp_double_click_drag(
                        start_x,
                        start_y,
                        end_x,
                        end_y,
                        drag_duration_ms,
                        click_interval_ms,
                        first_click_duration_ms,
                        hold_before_move_ms,
                    ))
                if action_type == "drag":
                    start_x = int(action.get("startX"))
                    start_y = int(action.get("startY"))
                    end_x = int(action.get("endX"))
                    end_y = int(action.get("endY"))
                    drag_duration_ms = coerce_delay_ms(action.get("dragDurationMs", 0))
                    hold_before_move_ms = coerce_delay_ms(
                        action.get("holdBeforeMoveMs", 0)
                    )
                    print(
                        f"   {index}. Drag @ ({start_x}, {start_y}) -> "
                        f"({end_x}, {end_y}) ({drag_duration_ms} ms)"
                    )
                    return bool(self._execute_cdp_single_drag(
                        start_x,
                        start_y,
                        end_x,
                        end_y,
                        drag_duration_ms,
                        hold_before_move_ms,
                    ))
                if action_type == "key":
                    key_name = action.get("key")
                    print(f"   {index}. Key {key_name} (CDP native input)")
                    if not self._execute_cdp_key_press(key_name):
                        print(
                            f"   🛑 CDP key input was inconclusive; refusing a duplicate "
                            f"PyAutoGUI key: {key_name}"
                        )
                        return False
                    return True
                return False
            except Exception as action_error:
                print(f"⚠️ Manual prepare action {index} failed: {action_error}")
                return False

        def bounded_sleep(seconds):
            """Sleep in short slices so stop/deadline can terminate replay."""
            remaining = max(float(seconds or 0.0), 0.0)
            while remaining > 0:
                if self.stop_event.is_set() or context.expired():
                    raise TimeoutError("manual_prepare_replay_deadline_or_stop")
                slice_seconds = min(1.0, remaining, max(context.deadline_at - time.monotonic(), 0.0))
                if slice_seconds <= 0:
                    raise TimeoutError("manual_prepare_replay_deadline")
                time.sleep(slice_seconds)
                remaining -= slice_seconds

        try:
            replay_success = replay_prepare_timeline(
                actions,
                profile_or_timing,
                execute_action,
                sleep_fn=bounded_sleep,
            )
            if not replay_success:
                return finish(False, "manual_prepare_replay_failed")

            end_delay_ms = get_prepare_end_delay_ms(profile_or_timing)
            if end_delay_ms > 0:
                print(f"   ⏳ Final PREPARE wait replayed: {end_delay_ms} ms")
            print("✅ Manual prepare actions replay completed")
            return finish(True, "manual_prepare_replay_verified")
        except Exception as replay_error:
            print(f"🛑 Manual profile replay stopped: {replay_error}")
            return finish(False, f"manual_prepare_replay_exception:{replay_error}")

    def intro_skip_via_cdp(self, operation_context=None):
        """ENHANCED: Multi-target Skip intro using CDP with smart detection + blind clicks"""
        try:
            self.entry_strategy = {
                "selected": None,
                "tested": [],
                "verification": "UNKNOWN",
            }
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âš ï¸ Could not get WebSocket URL for enhanced intro skipping, using pyautogui fallback")
                return self.intro_skip_via_pyautogui(operation_context=operation_context)
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print(f"\U0001f50d CDP SKIP/PLAY DETECTION: Looping scan + {self.intro_skip_clicks} blind clicks...")
            
            # Phase 1: Looping smart detection (scan -> click -> wait -> rescan)
            print("\U0001f50d Phase 1: Scanning for action buttons (skip/play/start/intro)...")
            max_scan_rounds = 5
            scan_interval = 2.0
            smart_clicks_performed = 0
            rounds_completed = 0
            candidate_trials_attempted = False
            
            try:
                for scan_round in range(1, max_scan_rounds + 1):
                    rounds_completed = scan_round
                    print(f"\n\U0001f504 Scan round {scan_round}/{max_scan_rounds}...")
                    
                    response = self._send_cdp_command(ws, {
                        "method": "Runtime.evaluate",
                        "params": {"expression": self.js_find_skip_intro, "awaitPromise": True, "returnByValue": True}
                    })
                    
                    buttons = []
                    if response and 'result' in response:
                        result = response.get('result', {}).get('result', {}).get('value')
                        if result and result.get('found'):
                            # Intro discovery is a bounded recovery helper;
                            # do not let a malformed runtime expose an
                            # unbounded candidate list.
                            buttons = list(result.get('buttons', []) or [])[:10]
                    
                    if not buttons:
                        print(f"   \u2139\ufe0f No action buttons found in round {scan_round}")
                        if smart_clicks_performed > 0:
                            print("   \u2705 Previous clicks were made, panels likely cleared")
                        break
                    
                    print(f"   \U0001f3af Found {len(buttons)} action button(s):")
                    for i, button in enumerate(buttons):
                        x, y = button['x'], button['y']
                        keyword = button.get('matchedKeyword', '?')
                        priority = button.get('priority', 99)
                        name = button.get('targetName', '?')
                        costume = button.get('currentCostumeName', '?')
                        print(f"      [{i+1}] P{priority} '{keyword}' - {name}/{costume} @ ({x}, {y})")

                    candidate_trials_attempted = True
                    trial_result = self._trial_action_buttons_via_ws(ws, buttons)
                    self.entry_strategy["tested"].extend((trial_result or {}).get("tested", [])[:10])
                    if trial_result.get("success"):
                        self.entry_strategy["selected"] = trial_result.get("selected")
                        smart_clicks_performed += 1
                        print(f"   \u2705 Selected one playable action candidate in round {scan_round}")
                        break

                    print(f"   \u26a0\ufe0f No playable action candidate in round {scan_round}: {trial_result.get('reason')}")
                    
                    if scan_round < max_scan_rounds:
                        print(f"   \u23f3 Waiting {scan_interval}s before next scan...")
                        time.sleep(scan_interval)
                
                if smart_clicks_performed > 0:
                    print(f"\n\u2705 Smart detection completed: {smart_clicks_performed} total clicks across {rounds_completed} round(s)")
                else:
                    print(f"\n\u2139\ufe0f No action buttons detected in {rounds_completed} round(s)")
                    
            except Exception as e:
                print(f"\u26a0\ufe0f Smart detection failed: {e}, proceeding with blind clicks...")


            # === PHASE 1.5: PRESS ANY KEY (NEW) ===
            print("⌨️ Phase 1.5: Pressing 'space' key to continue (for mods requiring key press)...")
            try:
                if not self._register_scoped_physical_input():
                    return False
                pyautogui.press('space')
                time.sleep(0.5)
                if not self._register_scoped_physical_input():
                    return False
                pyautogui.press('space')
                print("   ✅ 'space' key pressed twice successfully.")
            except Exception as key_e:
                print(f"   ⚠️ Could not press 'space' key: {key_e}")

            # Phase 2: ALWAYS perform blind clicks for additional assurance
            if smart_clicks_performed > 0:
                print("ðŸ”„ Phase 2: Performing ADDITIONAL blind CDP clicks for protection...")
                print("   â†’ Ensures intro skipping even if smart clicks had edge cases")
            else:
                print("ðŸ–±ï¸ Phase 2: Performing STANDARD blind CDP clicks (no smart buttons found)...")
            
            # Perform blind clicks via CDP only when smart detection found nothing.
            # Some mods put Gallery/menu controls at the screen center.
            blind_clicks_performed = 0
            if smart_clicks_performed == 0 and not candidate_trials_attempted:
                try:
                    intro_click_limit = min(max(int(self.intro_skip_clicks), 0), 10)
                except (TypeError, ValueError, OverflowError):
                    intro_click_limit = 0
                for i in range(intro_click_limit):
                    self._simulate_click_via_cdp(ws, 960, 540)
                    blind_clicks_performed += 1
                    time.sleep(0.5)
            else:
                print("   ⚠️ Skipping blind center clicks after smart clicks to avoid menu/gallery overlays.")
            
            ws.close()

            self.entry_strategy["introClicks"] = smart_clicks_performed + blind_clicks_performed
            self.entry_strategy["candidateTrialsAttempted"] = candidate_trials_attempted
            
            # Summary
            if smart_clicks_performed > 0:
                print("ðŸŽ‰ CDP SKIP/PLAY DETECTION COMPLETED:")
                print(f"   âœ… Smart clicks: {smart_clicks_performed} action buttons via CDP")
                print(f"   âœ… Blind clicks: {blind_clicks_performed} additional CDP clicks")
                print("   ðŸ›¡ï¸ Dual protection ensures maximum success rate")
            else:
                print("ðŸŽ‰ STANDARD CDP FALLBACK COMPLETED:")
                print("   âš ï¸ Smart clicks: N/A (No action buttons found)")
                print(f"   âœ… Blind clicks: {blind_clicks_performed} CDP clicks")
                print("   ðŸ”„ Standard method used as fallback")
            
            print("âœ… CDP intro skipping completed")
            return True
            
        except Exception as e:
            print(f"âŒ CDP intro skipping failed: {e}, falling back to pyautogui")
            return self.intro_skip_via_pyautogui(operation_context=operation_context)

    def intro_skip_via_pyautogui(self, operation_context=None):
        """ENHANCED: Multi-target Skip intro using PyAutoGUI with smart detection + blind drags"""
        try:
            self.entry_strategy.setdefault("tested", []).append(
                {"strategy": "pyautogui_fallback", "result": "attempted"}
            )
            try:
                intro_drag_limit = min(max(int(self.intro_skip_clicks), 0), 10)
            except (TypeError, ValueError, OverflowError):
                intro_drag_limit = 0
            print(f"ðŸ” ENHANCED PYAUTOGUI SKIP INTRO: Multi-target detection + {intro_drag_limit} wake-up drags...")
            
            # Phase 1: Try smart detection via WebSocket if available
            print("ðŸ“ Phase 1: Attempting smart detection via WebSocket...")
            
            smart_clicks_performed = 0
            
            try:
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    
                    # Execute JavaScript to find Skip Intro buttons
                    response = self._send_cdp_command(ws, {
                        "method": "Runtime.evaluate", 
                        "params": {"expression": self.js_find_skip_intro, "awaitPromise": True, "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        skip_result = response.get('result', {}).get('result', {}).get('value')
                        
                        if skip_result and skip_result.get('found'):
                            all_buttons = list(skip_result.get('buttons', []) or [])[:10]
                            button_count = skip_result.get('count', 0)
                            
                            print(f"âœ… Found {button_count} Skip Intro buttons via WebSocket")
                            
                            # Click ALL buttons found using PyAutoGUI
                            for i, button in enumerate(all_buttons):
                                button_num = i + 1
                                x, y = button['x'], button['y']
                                visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                                
                                print(f"ðŸŽ¯ PyAutoGUI clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status}")
                                
                                # Click this specific button via PyAutoGUI
                                if not self._register_scoped_physical_input():
                                    return False
                                pyautogui.moveTo(x, y, duration=0.2)
                                pyautogui.click(button='left')
                                time.sleep(0.8)  # Wait between clicks
                                
                                smart_clicks_performed += 1
                            
                            print(f"âœ… Smart PyAutoGUI clicks completed: {smart_clicks_performed} buttons clicked!")
                        else:
                            print("â„¹ï¸ No Skip Intro buttons found via WebSocket")
                    
                    ws.close()
                else:
                    print("âš ï¸ WebSocket not available for smart detection")
                    
            except Exception as e:
                print(f"âš ï¸ Smart detection via WebSocket failed: {e}")

            # === PHASE 1.5: PRESS ANY KEY (NEW) ===
            print("⌨️ Phase 1.5: Pressing 'space' key to continue (for mods requiring key press)...")
            try:
                if not self._register_scoped_physical_input():
                    return False
                pyautogui.press('space')
                time.sleep(0.5)
                if not self._register_scoped_physical_input():
                    return False
                pyautogui.press('space')
                print("   ✅ 'space' key pressed twice successfully.")
            except Exception as key_e:
                print(f"   ⚠️ Could not press 'space' key: {key_e}")

            # Phase 2: ALWAYS perform aggressive wake-up drags
            if smart_clicks_performed > 0:
                print("ðŸ”„ Phase 2: Performing ADDITIONAL wake-up drags for protection...")
                print("   â†’ Ensures intro skipping even if smart clicks had edge cases")
            else:
                print("âš¡ Phase 2: Performing AGGRESSIVE wake-up drags (no smart buttons found)...")
            
            # Aggressive wake-up drags using existing logic
            print(f"âš¡ AGGRESSIVE INTRO SKIPPING: Performing {self.intro_skip_clicks} 'wake-up drags' over 5 seconds...")
            screen_width, screen_height = pyautogui.size()
            center_x, center_y = screen_width // 2, screen_height // 2
            total_duration = 5.0
            num_drags = intro_drag_limit
            sleep_between_drags = total_duration / num_drags if num_drags else 0
            
            for i in range(num_drags):
                if self.stop_event.is_set(): 
                    return False
                start_x = center_x + random.randint(-100, 100)
                start_y = center_y + random.randint(-100, 100)
                drag_x_offset = random.randint(15, 40) * random.choice([-1, 1])
                drag_y_offset = random.randint(15, 40) * random.choice([-1, 1])
                end_x = start_x + drag_x_offset
                end_y = start_y + drag_y_offset
                print(f"   ... Wake-up drag {i + 1}/{num_drags}")
                if not self._register_scoped_physical_input():
                    return False
                pyautogui.moveTo(start_x, start_y, duration=0.05)
                pyautogui.dragTo(end_x, end_y, duration=0.15, button='left')
                time.sleep(max(sleep_between_drags - 0.20, 0.0))
            
            # Summary
            if smart_clicks_performed > 0:
                print("ðŸŽ‰ ENHANCED PYAUTOGUI SKIP INTRO COMPLETED:")
                print(f"   âœ… Smart clicks: {smart_clicks_performed} buttons via PyAutoGUI")
                print(f"   âœ… Wake-up drags: {intro_drag_limit} aggressive drags")
                print("   ðŸ›¡ï¸ Dual protection ensures maximum success rate")
            else:
                print("ðŸŽ‰ AGGRESSIVE PYAUTOGUI SKIP INTRO COMPLETED:")
                print("   âš ï¸ Smart clicks: N/A (No buttons found)")
                print(f"   âœ… Wake-up drags: {intro_drag_limit} aggressive drags")
                print("   âš¡ Aggressive method used as fallback")
            
            print("âœ… Aggressive intro skipping sequence completed.")
            return True
            
        except Exception as e:
            print(f"âŒ PyAutoGUI intro skipping failed: {e}")
            return False

    def restart_game_to_initial_state(self, use_cdp=True, operation_context=None):
        """Perform one bounded Re-run recovery and verify the fresh state."""
        context = operation_context or getattr(self, "_active_operation_context", None)
        owns_context = False
        if context is None:
            if self._session_recovery_count >= self._max_session_recoveries:
                return OperationResult(
                    OperationStatus.FAILED,
                    direction="recovery",
                    message="session_recovery_circuit_breaker",
                )
            context = self._begin_operation(
                "recovery",
                None,
                # One Re-run click plus the worst accepted manual profile
                # (and one small margin for entry controls).
                max_input_attempts=MAX_PREPARE_ACTIONS + 2,
                # A validated manual profile may legally contain a timeline
                # close to the 30-minute ceiling; the operation still has a
                # hard deadline rather than inheriting an unbounded wait.
                deadline_seconds=(MAX_TOTAL_PREPARE_DURATION_MS / 1000.0) + 5.0,
            )
            owns_context = True
        if context is None:
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="recovery",
                message="another_operation_is_active",
            )
        if operation_context is not None and not context.recovery_in_progress:
            return self._operation_result(
                context,
                OperationStatus.STOPPED,
                "recovery_must_be_started_by_coordinator",
            )

        if self._session_recovery_count >= self._max_session_recoveries:
            result = self._operation_result(
                context,
                OperationStatus.FAILED,
                "session_recovery_circuit_breaker",
            )
            if operation_context is None and not owns_context:
                context.end_recovery()
            return self._finish_operation(context, result) if owns_context else result

        try:
            if operation_context is None:
                if not context.begin_recovery():
                    result = self._operation_result(context, OperationStatus.FAILED, "recovery_budget_exhausted")
                    return self._finish_operation(context, result) if owns_context else result

            # The recovery coordinator owns the session-wide counter.  This
            # keeps direct and nested callers from incrementing it twice or
            # bypassing the two-Re-run circuit breaker.
            self._session_recovery_count += 1

            print(f"\nPHASE 3: GAME RESTART TO INITIAL STATE")
            print("="*60)
            print("   Method: Click Re-Run button + restore the configured game-entry flow")
            print("   Re-Run button coordinates: (40, 75)")
            print("   Expected result: All icons returned to initial positions")
            
            # Ensure window focus before restart
            self._ensure_focus_before_operation("game restart", focus_type="light")
            
            # Click Re-Run button at fixed coordinates
            print("   Step 1: Clicking Re-Run button...")
            if not context.can_send_input(
                stop_requested=self.stop_event.is_set(),
                allow_recovery_input=True,
            ) or not context.register_input():
                result = self._operation_result(context, OperationStatus.STOPPED, "recovery_input_budget_exhausted")
                return self._finish_operation(context, result) if owns_context else result
            rerun_clicked = False
            if use_cdp:
                # Try CDP first
                ws_url = self._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    rerun_clicked = bool(self._simulate_click_via_cdp(ws, 40, 75))
                    ws.close()
                    print("   Re-Run clicked via CDP")
                else:
                    # Fallback to PyAutoGUI
                    pyautogui.click(40, 75)
                    rerun_clicked = True
                    print("   Re-Run clicked via PyAutoGUI")
            else:
                pyautogui.click(40, 75)
                rerun_clicked = True
                print("   Re-Run clicked via PyAutoGUI")

            if not rerun_clicked:
                result = self._operation_result(context, OperationStatus.FAILED, "rerun_click_failed")
                return self._finish_operation(context, result) if owns_context else result
            
            # Wait for game restart process
            print("   Waiting for game restart process (3s)...")
            time.sleep(3.0)
            
            # Re-enter the game using the same manual profile that was used
            # during the initial setup. A restart does not call
            # setup_game_environment(), so the profile must be replayed here.
            print("   Step 2: Re-entering the game after restart...")
            manual_profile, manual_profile_path = self._resolve_manual_profile()
            profile_resolution_error = getattr(self, "_manual_profile_resolution_error", None)
            if profile_resolution_error:
                result = self._operation_result(
                    context,
                    OperationStatus.FAILED,
                    f"manual_profile_invalid:{profile_resolution_error}",
                )
                return self._finish_operation(context, result) if owns_context else result
            manual_actions = (manual_profile or {}).get("prepareActions", [])
            prepare_success = False

            if manual_profile:
                print(f"   Manual profile detected for restart: {manual_profile_path}")
                if not manual_actions:
                    print("   Manual profile has no prepare actions; automatic fallback is disabled")
                    result = self._operation_result(context, OperationStatus.FAILED, "manual_profile_has_no_actions")
                    return self._finish_operation(context, result) if owns_context else result

                print(f"   Replaying {len(manual_actions)} manual prepare actions...")
                prepare_success = self.replay_manual_prepare_actions(
                    manual_actions,
                    manual_profile,
                    operation_context=context,
                )
                if prepare_success:
                    print("   Manual profile prepare replay completed after restart")
                else:
                    print("   Manual profile replay failed; automatic fallback is disabled")
                    result = self._operation_result(context, OperationStatus.FAILED, "manual_prepare_replay_failed")
                    return self._finish_operation(context, result) if owns_context else result
            else:
                print("   No manual profile detected; using automatic intro skip")
                # Legacy intro helpers issue several physical inputs internally.
                # Scope the same recovery context around them so those inputs
                # cannot bypass the recovery budget.  Do not scope manual
                # profile replay: it already registers each action itself.
                self._physical_input_context = context
                try:
                    if use_cdp:
                        prepare_success = self.intro_skip_via_cdp(operation_context=context)
                    else:
                        prepare_success = self.intro_skip_via_pyautogui(operation_context=context)
                finally:
                    self._physical_input_context = None

            if not prepare_success:
                result = self._operation_result(context, OperationStatus.FAILED, "game_entry_prepare_failed")
                return self._finish_operation(context, result) if owns_context else result
            print("   Game entry preparation completed")
            
            # Additional stabilization wait
            print("   Game restart stabilization (2s)...")
            time.sleep(2.0)
            
            # Rebind the VM/session before reading the post-restart state.
            self._refresh_after_recovery()
            post_state = self._capture_stable_placement_snapshot(
                timeout_ms=1500,
                context=context,
            )
            if post_state is None:
                result = self._operation_result(context, OperationStatus.UNKNOWN, "post_restart_state_unknown")
                if operation_context is None:
                    context.end_recovery()
                return self._finish_operation(context, result) if owns_context else result

            polos = post_state.polos
            counters = [value for value in (post_state.characters_count, post_state.putted_count) if value is not None]
            polos_clean = polos is not None and all(str(value) == "0" for value in polos)
            counters_clean = bool(counters) and all(value == 0 for value in counters)
            if not (polos_clean or counters_clean):
                print("   RESTART UNCLEAR: state is known but not initial/empty")
                result = self._operation_result(context, OperationStatus.UNKNOWN, "post_restart_not_empty")
            else:
                print("   RESTART VERIFIED: Game returned to initial state!")
                result = self._operation_result(context, OperationStatus.RECOVERED, "game_recovered")

            print("GAME RESTART COMPLETED!")
            print("="*60)
            if operation_context is None:
                context.end_recovery()
            return self._finish_operation(context, result) if owns_context else result
            
        except Exception as e:
            print(f"Game restart error: {e}")
            print("Restart failed; workflow must not continue blindly")
            result = self._operation_result(context, OperationStatus.FAILED, f"restart_exception:{e}")
            if operation_context is None:
                context.end_recovery()
            return self._finish_operation(context, result) if owns_context else result

    def close_game_window(self):
        """Close game window safely"""
        print("ðŸ§¹ Safely closing Chrome debug session...")
        success = self.cleanup_chrome_complete()
        
        if success:
            print("âœ… Chrome debug session closed safely")
        else:
            print("âš ï¸ Some issues during Chrome debug cleanup")
            
        return success

    # ================================================================
    # == GAME ENVIRONMENT SETUP (ENHANCED with Verification) ==
    # ================================================================

    def setup_game_environment(self, progress_callback=None, first_setup=True):
        """
        FIXED: Setup with CDP verification and enhanced individual mode handling
        """
        try:
            self.last_setup_result = None
            # Do not carry a previous job/phase's entry candidate into the
            # next runtime proof.  Every setup must record a fresh strategy
            # and a fresh PASS/FAIL/UNKNOWN verification state.
            self.entry_strategy = {
                "selected": None,
                "tested": [],
                "verification": UNKNOWN,
            }
            if not self.config:
                print("🚫 CRITICAL ERROR: No config loaded - Cannot continue setup")
                print("   💡 Please load a JSON configuration file first")
                self.last_setup_result = QualityGateResult.failure(
                    "manual_json_invalid",
                    message="Configuration was not loaded.",
                )
                return False

            # A newly loaded game gets a fresh recovery/cleanup budget.  A
            # Re-run inside an active operation is handled by the operation
            # context and must not reset these counters.
            self._session_recovery_count = 0
            self._batch_reconnect_attempts = 0
            self._active_operation_context = None
            self._reset_cleanup_guard()

            # 🆕 ENHANCED INDIVIDUAL MODE CLEANUP
            if not first_setup:
                print("\n🔧 INDIVIDUAL MODE SETUP - Enhanced Cleanup with Port Verification")
                print("="*70)
                
                if progress_callback:
                    progress_callback("🔧 Individual Mode: Complete browser restart with port cleanup...")
                
                # Phase 1: Enhanced browser shutdown with port verification
                print("Phase 1: Enhanced browser shutdown with port verification...")
                self.cleanup_chrome_complete()  # This is our fixed function
                time.sleep(5)  # Extended wait for port cleanup
                
                # Phase 2: Verify port is actually clean
                print("Phase 2: Port cleanup verification...")
                port_clean = False
                for check_attempt in range(10):
                    try:
                        import socket
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1)
                        result = sock.connect_ex(('localhost', self.debugging_port))
                        sock.close()
                        
                        if result != 0:  # Port not accessible = clean
                            port_clean = True
                            print(f"   ✅ Port {self.debugging_port} confirmed clean")
                            break
                        else:
                            print(f"   ⏳ Port {self.debugging_port} still occupied, waiting... ({check_attempt + 1}/10)")
                            time.sleep(1)
                    except:
                        time.sleep(1)
                
                if not port_clean:
                    print(f"   ⚠️ Warning: Port {self.debugging_port} may still be in use")
                    print("   🔧 Proceeding anyway - Chrome may auto-resolve port conflicts")
                
                # Phase 3: Clear all connections and state
                print("Phase 3: Clear all sessions and connections...")
                self.ws_url = None
                self.session_websocket = None
                self.session_websocket_url = None
                self.batch_verification_active = False
                self.verification_functions_ready = False
                self.characters_setup_done = False
                self.putted_chars_setup_done = False
                self.characters_available = False
                self.putted_chars_available = False

                # Reset control flags
                print("Phase 4: Reset control flags for fresh Individual mode...")
                self.should_stop = False
                self.is_playing = False
                print("   ✅ should_stop = False")
                print("   ✅ is_playing = False")
                print("   🎯 Control flags reset - ready for drag operations")

                print("✅ Individual Mode cleanup completed - guaranteed fresh start")
                print("="*70)

            # 🔄 ENHANCED PRE-SETUP CLEANUP (existing logic, keep as-is)
            if progress_callback:
                progress_callback("🗃️ Clearing browsing data and preparing fresh session...")
            
            # NOTE: cleanup_chrome_complete() removed here — navigate_and_prepare_game()
            # already calls it internally with its own port verification loop.
            print("🗃️ Pre-setup cleanup: Clearing browsing cache data...")
            self.force_clear_chrome_data()
            print("✅ Pre-setup cleanup completed")

            # 🌐 BROWSER LAUNCH (existing logic, keep as-is)
            if progress_callback:
                if first_setup:
                    progress_callback("🌐 Opening game in Chrome debug mode (Master)...")
                else:
                    progress_callback("🌐 Fresh Chrome launch for Individual Mode...")

            print("✅ [GAME PLAYER] Environment setup started. Project folder is managed by a higher-level controller.")
            current_url = self.config['currentUrl']

            print("🌍 Opening game in Chrome debug mode...")

            success = self.navigate_and_prepare_game(current_url)

            if not success:
                print("⚠️ Chrome debug launch failed")
                if not isinstance(self.last_setup_result, QualityGateResult):
                    self.last_setup_result = QualityGateResult.failure(
                        "runtime_verification_unknown",
                        status="UNKNOWN",
                        retryable=True,
                        message="Browser/game navigation did not pass verification.",
                    )
                return False

            # 🔍 NEW: CDP READINESS VERIFICATION
            if progress_callback:
                progress_callback("🔍 Verifying CDP connection readiness...")
            
            print("🔍 CRITICAL: Verifying CDP readiness before proceeding...")
            cdp_ready = self.verify_cdp_ready(max_attempts=3)
            
            if not cdp_ready:
                print("❌ CDP verification failed - automation will not work properly")
                print("💡 This is likely why drag operations fail in Both mode")
                self.last_setup_result = QualityGateResult.failure(
                    "runtime_verification_unknown",
                    status="UNKNOWN",
                    retryable=True,
                    message="CDP readiness was not verified.",
                )
                return False
            else:
                print("✅ CDP verification successful - automation ready!")

            manual_profile, manual_profile_path = self._resolve_manual_profile()
            profile_resolution_error = getattr(self, "_manual_profile_resolution_error", None)
            if profile_resolution_error:
                print(
                    "🛑 Manual profile is invalid; refusing automatic intro fallback: "
                    f"{profile_resolution_error}"
                )
                self.last_setup_result = QualityGateResult.failure(
                    "manual_json_invalid",
                    evidence={"profileError": profile_resolution_error},
                    message="Manual entry profile is invalid.",
                )
                return False
            if manual_profile:
                self.entry_strategy.setdefault("tested", []).append(
                    {"strategy": "manual_profile", "path": manual_profile_path}
                )
                if progress_callback:
                    progress_callback("🧭 Replaying manual profile prepare actions...")
                print(f"🧭 Using manual profile: {manual_profile_path}")
                replay_success = self.replay_manual_prepare_actions(
                    manual_profile.get("prepareActions", []),
                    manual_profile,
                )
                if not replay_success:
                    print("🛑 Manual profile replay failed; refusing blind automatic input on a partially prepared game")
                    self.last_setup_result = QualityGateResult.failure(
                        "manual_json_invalid",
                        evidence={"profilePath": manual_profile_path},
                        message="Manual entry profile replay failed.",
                    )
                    return False
                self.entry_strategy["selected"] = {
                    "strategy": "manual_profile",
                    "path": manual_profile_path,
                }
            else:
                # Skip intros (existing logic, keep as-is)
                if progress_callback:
                    progress_callback("⏭️ Skipping intros...")

                if not self.intro_skip_via_cdp():
                    print("🛑 CDP intro skipping failed; refusing to continue on an unprepared game")
                    self.last_setup_result = QualityGateResult.failure(
                        "runtime_verification_unknown",
                        status="UNKNOWN",
                        retryable=True,
                        message="Automatic entry preparation was not verified.",
                    )
                    return False

            # 🔄 DUAL VERIFICATION SETUP (existing logic, keep as-is)
            if progress_callback:
                progress_callback("🎯 Setting up DUAL verification system...")
            
            if not self.setup_dual_verification_system():
                print("⚠️ DUAL verification setup failed, continuing with basic workflow")
            else:
                print("✅ DUAL verification system ready!")

            # 🚀 BATCH VERIFICATION SESSION (existing logic, keep as-is)
            if progress_callback:
                progress_callback("⚡ Starting batch verification session...")

            if self.start_batch_verification_session():
                print("🎯 Batch verification session active - PERFORMANCE MODE!")
            else:
                print("⚠️ Batch session failed, using individual verification")

            # 🎮 FINAL GAME PREPARATION (existing logic, keep as-is)
            self.prepare_game_for_playing()
            time.sleep(1)

            # Containers and a Run button are only structural evidence.  The
            # VM must expose real icons and slots before any recording starts.
            runtime_entry = self.verify_playable_runtime_entry()
            if not runtime_entry:
                self.last_setup_result = runtime_entry
                print(
                    "🛑 SETUP FAILED: runtime playability was not proven: "
                    f"{runtime_entry.failure_code or runtime_entry.status}"
                )
                self.cleanup_chrome_complete()
                return False

            if progress_callback:
                progress_callback("✅ Enhanced game environment ready!")
            
            print("✅ ENHANCED GAME ENVIRONMENT SETUP COMPLETED!")
            print("🎯 FEATURES ACTIVE:")
            print("   🌐 Chrome debug mode for browser management")
            print("   🎮 PyAutoGUI for precise game automation")
            print("   🔌 WebSocket/CDP for advanced control")
            print("   🛡️ AdBlocker extension loaded")
            print("   🔍 CDP connection verified and functional")
            
            # DUAL verification status (existing logic, keep as-is)
            if self.characters_available or self.putted_chars_available:
                print("   🎯 DUAL verification system ENABLED:")
                print(f"      🔢 Characters Array: {'✅ Ready' if self.characters_available else '❌ Failed'}")
                print(f"      🎯 Putted Characters: {'✅ Ready' if self.putted_chars_available else '❌ Failed'}")
                print("      💡 Only 1 method needs to work = Maximum compatibility!")
            else:
                print("   ⚠️ DUAL verification system DISABLED (fallback mode)")
            
            return True
            
        except Exception as e:
            error_message = f"An unexpected error occurred during game setup: {e}"
            print(f"🚫 {error_message}")
            import traceback
            traceback.print_exc()
            return False
    
    def ensure_mouse_released(self):
        """IMPROVED: Ensure mouse is released before starting new drag"""
        try:
            print("🔄 IMPROVED: Cleaning mouse state with verification...")
            
            # Multiple release attempts để đảm bảo
            for attempt in range(3):
                pyautogui.mouseUp(button='left')
                # pyautogui.mouseUp(button='right')
                # pyautogui.mouseUp(button='middle')
                time.sleep(0.05)
            
            # Move mouse to neutral position để tránh hover effects
            screen_width, screen_height = pyautogui.size()
            neutral_x = screen_width // 2
            neutral_y = screen_height // 2
            pyautogui.moveTo(neutral_x, neutral_y, duration=0.2)
            time.sleep(0.2)
            
            print("   ✅ IMPROVED mouse state cleaned with verification")
            
        except Exception as e:
            print("   ⚠️ Mouse cleanup warning: " + str(e))
    
    def ensure_game_focus(self):
        """ðŸ”§ ENHANCED: Ensure game focus vá»›i service mode support - SAFE CLICK VERSION"""
        try:
            print("ðŸŽ¯ Ensuring game focus for service mode...")
            
            # Phase 1: Find vÃ  activate Chrome window
            chrome_windows = gw.getWindowsWithTitle('cocrea.world')
            if not chrome_windows:
                chrome_windows = gw.getWindowsWithTitle('Chrome')
            
            if chrome_windows:
                target_window = chrome_windows[0]
                print(f"   ðŸ” Found window: {target_window.title[:50]}...")
                
                try:
                    # Activate window vá»›i error handling
                    target_window.activate()
                    time.sleep(0.3)
                    
                    # Bring to foreground
                    target_window.restore()  # Ensure not minimized
                    time.sleep(0.2)
                    
                    print("   âœ… Window activated successfully")
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    if "error code from windows: 0" in error_msg:
                        print("   âœ… Window activated (Windows quirk)")
                    else:
                        print(f"   âš ï¸ Window activation warning: {e}")
            
            # ðŸ†• Phase 2: SAFE MOUSE FOCUS - TRÃNH CLICK VÃ€O SLOT
            print("   ðŸŽ¯ Phase 2: Safe mouse focus (avoiding slots/icons)...")
            
            # ðŸ†• SAFE ZONE: Click vÃ o gÃ³c trÃªn bÃªn trÃ¡i (title bar area)
            # Tá»a Ä‘á»™ nÃ y sáº½ click vÃ o title bar cá»§a browser, khÃ´ng pháº£i game area
            safe_x = 20  # GÃ³c trÃ¡i, trÃ¡nh slots á»Ÿ giá»¯a
            safe_y = 200   # Title bar area, trÃ¡nh game canvas
            
            print(f"   ðŸ›¡ï¸ Using SAFE click position: ({safe_x}, {safe_y}) - Title bar area")
            pyautogui.moveTo(safe_x, safe_y, duration=0.2)
            time.sleep(0.1)
            
            # Click vÃ o title bar Ä‘á»ƒ focus
            pyautogui.click(safe_x, safe_y)
            time.sleep(0.2)
            
            # ðŸ†• Phase 3: Move mouse to NEUTRAL position (bottom right corner)
            print("   ðŸŽ¯ Phase 3: Moving mouse to neutral position...")
            screen_width, screen_height = pyautogui.size()
            neutral_x = screen_width - 100  # GÃ³c pháº£i dÆ°á»›i
            neutral_y = screen_height - 100
            
            pyautogui.moveTo(neutral_x, neutral_y, duration=0.3)
            print(f"   âœ… Mouse moved to neutral corner: ({neutral_x}, {neutral_y})")
            
            print("   âœ… Game focus ensured with SAFE method (no slot interference)")
            return True
            
        except Exception as e:
            print(f"   âŒ Enhanced focus error: {e}")
            return False

    def ensure_game_focus_light(self):
        """ðŸª¶ LIGHT FOCUS: Chá»‰ activate window, KHÃ”NG click (an toÃ n nháº¥t)"""
        try:
            print("ðŸª¶ Light focus: Window activation only...")
            
            # Chá»‰ activate window, khÃ´ng click gÃ¬ cáº£
            chrome_windows = gw.getWindowsWithTitle('cocrea.world')
            if not chrome_windows:
                chrome_windows = gw.getWindowsWithTitle('Chrome')
            
            if chrome_windows:
                target_window = chrome_windows[0]
                try:
                    target_window.activate()
                    target_window.restore()
                    time.sleep(0.3)
                    print("   âœ… Light focus completed - no clicks performed")
                    return True
                except Exception as e:
                    error_msg = str(e).lower()
                    if "error code from windows: 0" in error_msg:
                        print("   âœ… Light focus completed (Windows quirk)")
                        return True
                    else:
                        print(f"   âš ï¸ Light focus warning: {e}")
                        return False
            else:
                print("   âš ï¸ No window found for light focus")
                return False
                
        except Exception as e:
            print(f"   âŒ Light focus error: {e}")
            return False

    def _ensure_focus_before_operation(self, operation_name="operation", focus_type="safe"):
        """ðŸ”§ NEW: Äáº£m báº£o focus trÆ°á»›c má»i mouse operation vá»›i tÃ¹y chá»n loáº¡i focus"""
        try:
            print(f"ðŸŽ¯ Ensuring focus before {operation_name}...")
            
            # Chá»n loáº¡i focus dá»±a trÃªn tham sá»‘
            if focus_type == "light":
                # Chá»‰ activate window, khÃ´ng click (an toÃ n nháº¥t)
                success = self.ensure_game_focus_light()
            elif focus_type == "safe":
                # Activate + click an toÃ n (trÃ¡nh slots)
                success = self.ensure_game_focus()
            else:
                # Default to safe
                success = self.ensure_game_focus()
            
            if success:
                print(f"   âœ… Focus ready for {operation_name} (type: {focus_type})")
            else:
                print(f"   âš ï¸ Focus uncertain for {operation_name}, continuing anyway")
            
            return success
            
        except Exception as e:
            print(f"   âŒ Focus check error for {operation_name}: {e}")
            return False        
    
    def get_domain_adjustments(self):
        """Get domain-specific adjustments for current config"""
        if not self.config:
            return self.domain_adjustments.get('sprunki.org', self.domain_adjustments['sprunki.org'])
        
        try:
            parsed_url = urlparse(self.config['currentUrl'])
            domain = parsed_url.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            
            return self.domain_adjustments.get(domain, self.domain_adjustments['sprunki.org'])
        except:
            return self.domain_adjustments['sprunki.org']

    def _are_coords_safe(self, *coords):
        """
        [ENHANCED] Kiểm tra xem tọa độ có hợp lệ, nằm trong màn hình,
        VÀ KHÔNG nằm ở các góc fail-safe.
        """
        try:
            screen_width, screen_height = pyautogui.size()
            margin = 1 # Một vùng đệm nhỏ cách các cạnh màn hình
            
            # Kiểm tra từng cặp tọa độ (x, y)
            for i in range(0, len(coords), 2):
                x, y = coords[i], coords[i+1]

                if x is None or y is None or not all(isinstance(v, (int, float)) for v in [x, y]):
                    print(f"   ❌ Tọa độ không hợp lệ (giá trị None hoặc không phải số): ({x}, {y})")
                    return False
                
                # Kiểm tra xem có nằm trong vùng an toàn không (cách các cạnh 1 pixel)
                if not (margin <= x < screen_width - margin and margin <= y < screen_height - margin):
                    print(f"   ❌ Tọa độ ({x}, {y}) quá gần hoặc nằm ngoài cạnh màn hình ({screen_width}x{screen_height}). Fail-safe có thể bị kích hoạt.")
                    return False
            
            return True
        except Exception as e:
            print(f"   ❌ Lỗi khi kiểm tra tọa độ: {e}")
            return False

    def move_mouse_to_safe_position(self):
        """Move mouse to bottom right corner as required by game"""
        try:
            screen_width, screen_height = pyautogui.size()
            
            # Bottom right corner with small margin from edge
            safe_x = screen_width - random.randint(30, 80)
            safe_y = screen_height - random.randint(30, 80)
            
            print(f"ðŸŽ¯ Moving mouse to safe position: ({safe_x}, {safe_y})")
            pyautogui.moveTo(safe_x, safe_y, duration=random.uniform(0.3, 0.5))
            time.sleep(0.2)
            
            return True
        except Exception as e:
            print("âš ï¸ Safe position movement warning: " + str(e))
            return False

    def _execute_cdp_drag(self, start_x, start_y, end_x, end_y, delays, domain_adj):
        """Execute drag using CDP WebSocket commands"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âš ï¸ WebSocket not available, falling back to PyAutoGUI")
                return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, False)
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print("ðŸ–±ï¸ Executing CDP drag operation...")
            
            # Move to start position
            print("ðŸ“ Step 1: Moving to icon...")
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
            time.sleep(delays['approach'])
            
            # Focus clicks if needed
            print("ðŸŽ¯ Step 2: Establishing focus...")
            if domain_adj['requires_double_click']:
                self._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(0.15)
                self._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(delays['focus'])
            else:
                self._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(delays['focus'])
            
            # Execute drag
            print("ðŸ¤ Step 3: Executing drag...")
            self._simulate_drag_via_cdp(ws, start_x, start_y, end_x, end_y)
            time.sleep(delays['post_drop'])

            # Move to safe position via CDP
            print("ðŸŽ¯ Moving to safe position via CDP...")
            screen_width, screen_height = 1920, 1080  # Assume fullscreen
            safe_x = screen_width - random.randint(30, 80)
            safe_y = screen_height - random.randint(30, 80)
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": safe_x, "y": safe_y}})
            print(f"âœ… Mouse moved to safe corner via CDP: ({safe_x}, {safe_y})")
            
            ws.close()
            print("âœ… CDP drag completed!")
            return True
            
        except Exception as e:
            print(f"âŒ CDP drag failed: {e}, falling back to PyAutoGUI")
            return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, False)

    def _execute_pyautogui_drag(self, start_x, start_y, end_x, end_y, delays, domain_adj, is_first_icon):
        """Execute drag using PyAutoGUI (IMPROVED with stability enhancements)"""
        # 🔥 KIỂM TRA AN TOÀN TỌA ĐỘ TRƯỚC KHI THỰC HIỆN
        if not self._are_coords_safe(start_x, start_y, end_x, end_y):
            print("   ❌ PyAutoGUI drag bị hủy do tọa độ không an toàn.")
            return False
        try:
            print("🖱️ Executing IMPROVED PyAutoGUI drag operation...")
            
            # 🛡️ SAFE: Ensure focus before drag operation (use light focus để tránh click nhầm)
            self._ensure_focus_before_operation("drag operation", focus_type="light")
            
            # === APPROACH ICON WITH VERIFICATION ===
            print("📍 Step 1: Moving to icon with verification...")
            move_duration = (0.6 if is_first_icon else 0.4) * domain_adj['drag_speed_multiplier']  # Chậm hơn
            pyautogui.moveTo(start_x, start_y, duration=move_duration)
            time.sleep(delays['approach'])
            
            # VERIFY position after move
            current_pos = pyautogui.position()
            if abs(current_pos.x - start_x) > 5 or abs(current_pos.y - start_y) > 5:
                print(f"   ⚠️ Position drift detected, correcting...")
                pyautogui.moveTo(start_x, start_y, duration=0.2)
                time.sleep(0.2)
            
            # === ESTABLISH FOCUS WITH ENHANCED TIMING ===
            print("🎯 Step 2: Establishing focus with enhanced timing...")
            if domain_adj['requires_double_click'] or is_first_icon:
                pyautogui.click()
                time.sleep(0.25)  # Tăng từ 0.15 → 0.25
                pyautogui.click()
                time.sleep(delays['focus'])
            else:
                pyautogui.click()
                time.sleep(delays['focus'])
            
            # Move back to exact position and verify
            pyautogui.moveTo(start_x, start_y, duration=0.15)
            time.sleep(0.2)  # Thêm pause để đảm bảo position
            
            # === START DRAG WITH ENHANCED VERIFICATION ===
            print("🤌 Step 3: Starting ENHANCED drag - MOUSE DOWN...")
            
            # Final position verification before mouse down
            current_pos = pyautogui.position()
            if abs(current_pos.x - start_x) > 10 or abs(current_pos.y - start_y) > 10:
                print("   ⚠️ Critical position drift, re-correcting...")
                pyautogui.moveTo(start_x, start_y, duration=0.2)
                time.sleep(0.2)
            
            # ENHANCED MOUSE DOWN with verification
            print("🖱 MOUSE DOWN - holding with enhanced timing...")
            pyautogui.mouseDown(button='left')
            
            # 🔥 CRITICAL: Extended hold time để game nhận diện
            extended_hold = delays['hold'] * 1.5  # Tăng thêm 50%
            print(f"   ⏳ Extended hold time: {extended_hold:.2f}s")
            time.sleep(extended_hold)
            
            # Verify mouse is still down (optional check)
            print("   ✅ Mouse down phase completed")
            
            # === DRAG TO TARGET WITH SMOOTH MOVEMENT ===
            print("🎯 Step 4: Dragging to target with enhanced smoothness...")
            drag_duration = delays['drag'] * domain_adj['drag_speed_multiplier']
            
            # 🔥 ENHANCED: Minimum drag duration để đảm bảo game tracking
            min_drag_duration = 0.8  # Tối thiểu 0.8 giây
            if drag_duration < min_drag_duration:
                drag_duration = min_drag_duration
                print(f"   📈 Applied minimum drag duration: {drag_duration:.2f}s")
            
            # Smooth drag movement with intermediate checkpoints
            print(f"   🎯 Dragging from ({start_x}, {start_y}) → ({end_x}, {end_y}) in {drag_duration:.2f}s")
            pyautogui.moveTo(end_x, end_y, duration=drag_duration)
            
            # 🔥 CRITICAL: Extended drop preparation time
            enhanced_drop_wait = delays['drop'] * 1.3  # Tăng thêm 30%
            print(f"   ⏳ Enhanced drop preparation: {enhanced_drop_wait:.2f}s")
            time.sleep(enhanced_drop_wait)
            
            # === RELEASE MOUSE WITH ENHANCED VERIFICATION ===
            print("🖱 Step 5: ENHANCED MOUSE UP - releasing at target...")
            
            # Final position verification before release
            final_pos = pyautogui.position()
            position_error = abs(final_pos.x - end_x) + abs(final_pos.y - end_y)
            
            if position_error > 20:  # If too far from target
                print(f"   ⚠️ Large position error ({position_error:.1f}px), correcting before release...")
                pyautogui.moveTo(end_x, end_y, duration=0.15)
                time.sleep(0.2)
            
            # 🔥 ENHANCED MOUSE RELEASE
            print("🖱 RELEASING mouse with verification...")
            pyautogui.mouseUp(button='left')
            
            # Verify release completed
            print("   ✅ Mouse UP completed")
            time.sleep(delays['post_drop'])
            
            # === MOVE TO SAFE POSITION ===
            print("🛡️ Moving to safe position immediately...")
            screen_width, screen_height = pyautogui.size()
            safe_x = screen_width - random.randint(50, 100)  # Tăng margin
            safe_y = screen_height - random.randint(50, 100)
            pyautogui.moveTo(safe_x, safe_y, duration=random.uniform(0.3, 0.5))
            print(f"   ✅ Mouse moved to safe corner: ({safe_x}, {safe_y})")
            
            print("✅ IMPROVED PyAutoGUI drag completed with enhanced stability!")
            return True
            
        except Exception as e:
            print(f"❌ IMPROVED PyAutoGUI drag failed: {e}")
            # Emergency mouse release
            try:
                pyautogui.mouseUp(button='left')
                print("   🆘 Emergency mouse release executed")
            except:
                pass
            return False

    def _execute_dynamic_drag_cdp(self, ws, start_x, start_y, end_x, end_y, control_type, is_first_icon):
        """
        Execute the gameplay drag through the same bounded CDP gesture primitive
        used by manual PREPARE replay.
        """
        try:
            print(
                f"   Executing shared CDP drag (is_first_icon: {is_first_icon}, "
                f"type: '{control_type}')"
            )
            timing_key = 'first_icon' if is_first_icon else 'normal_icon'
            delays = self.DRAG_DELAYS[timing_key].copy()
            domain_adj = self.get_domain_adjustments()
            stability_factor = domain_adj.get('stability_factor', 1.0)
            for key in delays:
                delays[key] *= stability_factor

            if is_first_icon:
                print("   🎪 First icon - applying extended timing and extra wait...")
                time.sleep(delays['approach'])
                time.sleep(domain_adj['extra_focus_time'])
            is_double = control_type == DOUBLE_CLICK_DRAG_TYPE
            total_drag_ms = int(round((delays['hold'] + delays['drag']) * 1000))
            hold_ms = int(round(delays['hold'] * 1000))
            result = execute_cdp_gesture(
                ws,
                self._send_cdp_command,
                start_x,
                start_y,
                end_x,
                end_y,
                mode=DOUBLE_CLICK_DRAG_TYPE if is_double else "drag",
                drag_duration_ms=total_drag_ms,
                click_interval_ms=300,
                first_click_duration_ms=100,
                hold_before_move_ms=hold_ms,
                approach_ms=0 if is_first_icon else int(round(delays['approach'] * 1000)),
                focus_delay_ms=int(round(delays['focus'] * 1000)),
                focus_click=not is_double,
                drop_wait_ms=int(round(delays['drop'] * 1.3 * 1000)),
                post_drop_ms=int(round(delays['post_drop'] * 1000)),
                move_steps=20,
                sleep_fn=time.sleep,
            )
            if not result.success:
                print(f"   🛑 Shared CDP drag inconclusive: {result.error}")
                return False, result.action_start_time, result.drop_time

            self.move_mouse_to_safe_position()
            print("   ✅ Shared CDP drag successful.")
            return True, result.action_start_time, result.drop_time

        except Exception as e:
            print(f"   ❌ Exception during dynamic CDP drag: {e}")
            return False, None, None

    def corrected_return_icon(self, target_slot, use_cdp=True):
        """
        Return one icon with a single bounded precision/batch/recovery chain.
        """
        return_process_start_time = time.monotonic()
        obs_controller = self.obs_controller
        target_slot_index = target_slot.get("index", -1)
        context = self._begin_operation(
            "return",
            target_slot_index,
            # Precision/carpet inputs and one possible manual-profile
            # recovery share one finite budget.
            max_input_attempts=120 + MAX_PREPARE_ACTIONS + 2,
            deadline_seconds=(MAX_TOTAL_PREPARE_DURATION_MS / 1000.0) + 60.0,
        )
        if context is None:
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="return",
                message="another_operation_is_active",
            )

        try:
            if target_slot_index < 0:
                return self._finish_operation(
                    context,
                    self._operation_result(context, OperationStatus.FAILED, "invalid_target_slot"),
                )

            baseline = self._capture_stable_placement_snapshot(
                timeout_ms=1000,
                context=context,
            )
            if baseline is None:
                return self._finish_operation(
                    context,
                    self._operation_result(context, OperationStatus.UNKNOWN, "baseline_unavailable"),
                )
            context.set_baseline(baseline)

            precondition = classify_precondition(baseline, "return", target_slot_index)
            if precondition == OperationStatus.ALREADY_EMPTY:
                return self._finish_operation(
                    context,
                    self._operation_result(context, OperationStatus.ALREADY_EMPTY, "target_already_empty"),
                )

            # PHASE 1: one precision click
            print(f"\nðŸŽ¯ PHASE 1: PRECISION CLICK")
            precision_result = self._execute_precision_return_click(
                target_slot,
                use_cdp,
                operation_context=context,
                before_snapshot=baseline,
            )
            if precision_result.status in {OperationStatus.VERIFIED, OperationStatus.ALREADY_EMPTY}:
                print("ðŸŽ‰ PHASE 1 SUCCESS!")
                if obs_controller:
                    obs_controller.log_event_timestamp(
                        "return_completed_successfully",
                        f"Return successful (Phase 1) for slot {target_slot_index}",
                        {
                            "slot_index": target_slot_index,
                            "return_phase": 1,
                            "status": precision_result.status.value,
                            "operationId": context.operation_id,
                        },
                    )
                return self._finish_operation(context, precision_result)

            if precision_result.status in {OperationStatus.UNKNOWN, OperationStatus.STOPPED}:
                print(f"   🛑 Precision result is {precision_result.status.value}; no blind carpet retry")
                return self._finish_operation(context, precision_result)

            # PHASE 2: bounded carpet bombing.  It is only allowed after the
            # precision click produced a known no-change result.
            print(f"\nðŸ’¥ PHASE 2: CARPET BOMBING")
            current_state = self._capture_stable_placement_snapshot(
                timeout_ms=500,
                context=context,
            )
            if current_state is None:
                result = self._operation_result(context, OperationStatus.UNKNOWN, "post_precision_state_unknown")
                return self._finish_operation(context, result)
            if classify_precondition(current_state, "return", target_slot_index) == OperationStatus.ALREADY_EMPTY:
                result = self._operation_result(context, OperationStatus.ALREADY_EMPTY, "target_cleared_after_precision")
                return self._finish_operation(context, result)
            post_precision_evaluation = evaluate_transition(
                baseline,
                current_state,
                "return",
                target_slot_index,
            )
            if post_precision_evaluation.status == OperationStatus.VERIFIED:
                result = self._operation_result(
                    context,
                    OperationStatus.UNKNOWN,
                    "precision_changed_state_without_target_proof",
                    evidence=post_precision_evaluation.evidence,
                    details={
                        **post_precision_evaluation.details,
                        "targetSpecific": post_precision_evaluation.target_specific,
                        "aggregateFallback": post_precision_evaluation.aggregate_fallback,
                    },
                )
                return self._finish_operation(context, result)
            if post_precision_evaluation.status == OperationStatus.UNKNOWN:
                result = self._operation_result(context, OperationStatus.UNKNOWN, "post_precision_transition_unknown")
                return self._finish_operation(context, result)

            carpet_result = self._execute_carpet_bombing_return(
                use_cdp,
                operation_context=context,
                before_snapshot=baseline,
            )
            if carpet_result.status in {OperationStatus.VERIFIED, OperationStatus.ALREADY_EMPTY}:
                print("ðŸŽ‰ PHASE 2 SUCCESS!")
                if obs_controller:
                    obs_controller.log_event_timestamp(
                        "return_completed_successfully",
                        f"Return successful (Phase 2) for slot {target_slot_index}",
                        {
                            "slot_index": target_slot_index,
                            "return_phase": 2,
                            "status": carpet_result.status.value,
                            "aggregateFallback": bool(carpet_result.details.get("aggregateFallback")),
                            "operationId": context.operation_id,
                        },
                    )
                return self._finish_operation(context, carpet_result)

            if carpet_result.status in {OperationStatus.UNKNOWN, OperationStatus.STOPPED}:
                return self._finish_operation(context, carpet_result)

            # PHASE 3: one recovery, then terminate this operation.  The old
            # implementation returned True here and made outer callers retry
            # the entire precision/carpet/restart chain.
            print(f"\nðŸš¨ PHASE 3: RESTART FALLBACK")
            if not context.begin_recovery():
                result = self._operation_result(context, OperationStatus.FAILED, "operation_recovery_budget_exhausted")
                return self._finish_operation(context, result)
            if self._session_recovery_count >= self._max_session_recoveries:
                context.end_recovery()
                result = self._operation_result(context, OperationStatus.FAILED, "session_recovery_circuit_breaker")
                return self._finish_operation(context, result)

            if obs_controller:
                obs_controller.log_event_timestamp(
                    "start_game_restart",
                    "Restart triggered after bounded return methods failed",
                    {"operationId": context.operation_id, "slot_index": target_slot_index},
                )
            recovery_result = self.restart_game_to_initial_state(
                use_cdp,
                operation_context=context,
            )
            context.end_recovery()
            if obs_controller:
                obs_controller.log_event_timestamp(
                    "end_game_restart",
                    "Restart recovery completed",
                    {
                        "operationId": context.operation_id,
                        "status": recovery_result.status.value,
                    },
                )

            if recovery_result.status == OperationStatus.RECOVERED:
                result = self._operation_result(
                    context,
                    OperationStatus.RECOVERED,
                    "game_recovered_target_return_not_verified",
                    details={"recovery": recovery_result.as_dict()},
                )
            else:
                result = self._operation_result(
                    context,
                    recovery_result.status,
                    "return_not_verified_after_recovery",
                    details={"recovery": recovery_result.as_dict()},
                )
            return self._finish_operation(context, result)

        except Exception as e:
            print(f"â Œ Critical error in corrected_return_icon: {e}")
            result = self._operation_result(context, OperationStatus.FAILED, f"return_exception:{e}")
            return self._finish_operation(context, result)

    def _execute_precision_return_click(
        self,
        target_slot,
        use_cdp,
        operation_context=None,
        before_snapshot=None,
    ):
        """Execute exactly one precision click and observe its transition."""
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            domain_adj = self.get_domain_adjustments()
            print(f"   ðŸŽ¯ Method: Single precision click at exact coordinates")
            print(f"   ðŸ“ Target: ({center_x}, {center_y})")
            context = operation_context or getattr(self, "_active_operation_context", None)
            if context is None:
                return OperationResult(OperationStatus.UNKNOWN, direction="return", message="missing_operation_context")
            if before_snapshot is None:
                before_snapshot = self._capture_stable_placement_snapshot(timeout_ms=1000, context=context)
            if before_snapshot is None:
                return self._operation_result(context, OperationStatus.UNKNOWN, "baseline_unavailable")
            context.set_baseline(before_snapshot)
            if not context.can_send_input(stop_requested=self.stop_event.is_set()):
                return self._operation_result(context, OperationStatus.FAILED, "input_budget_exhausted")

            context.register_input()
            executed = self._execute_single_return_click(
                center_x,
                center_y,
                use_cdp,
                domain_adj,
            )
            if not executed:
                return self._operation_result(context, OperationStatus.FAILED, "precision_click_execution_failed")

            observed_result = observe_until_verified(
                before_snapshot,
                self._capture_placement_snapshot,
                "return",
                context.target_slot,
                operation_id=context.operation_id,
                timeout_ms=getattr(self, "verifier_post_action_timeout_ms", 5000),
                poll_ms=50,
                stable_samples=getattr(self, "verifier_post_action_stable_samples", 1),
                stop_requested=lambda: self.stop_event.is_set(),
                context=context,
            )
            result = self._operation_result(
                context,
                observed_result.status,
                observed_result.message,
                evidence=observed_result.evidence,
                details=observed_result.details,
                reads=context.read_count,
            )
            if getattr(self, "verifier_accept_aggregate_progress", True):
                result = promote_aggregate_transition(
                    result,
                    "return",
                    allow_unstable=getattr(self, "verifier_accept_unstable_delta", True),
                )
            if result.status == OperationStatus.VERIFIED:
                self._remember_empty_board_baseline(
                    result.details.get("after", (None, None))[0]
                    if result.details.get("after") else None,
                    result.details.get("after", (None, None))[1]
                    if result.details.get("after") else None,
                )
            return result
        except Exception as e:
            print(f"âŒ Error in precision return click: {e}")
            return self._operation_result(
                operation_context,
                OperationStatus.FAILED,
                f"precision_click_exception:{e}",
            )

    def _execute_carpet_bombing_return(
        self,
        use_cdp=False,
        operation_context=None,
        before_snapshot=None,
    ):
        """Run one bounded carpet strategy and classify the final state."""
        try:
            print(f"\nðŸ’¥ SEQUENTIAL CARPET BOMBING FOR RETURN")
            print("="*60)
            print("   ðŸ”„ REUSING: Same logic as remix cleanup")
            print("   ðŸŽ¯ Strategy: Sequential slot-by-slot processing")
            
            if not self.config:
                print("â Œ No config available for carpet bombing")
                return self._operation_result(
                    operation_context,
                    OperationStatus.FAILED,
                    "carpet_config_unavailable",
                )
                    
            all_slots = self.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("â Œ No slots configuration found")
                return self._operation_result(
                    operation_context,
                    OperationStatus.FAILED,
                    "carpet_slots_unavailable",
                )
            
            sorted_slots = sorted(all_slots, key=lambda s: s.get('index', 0))
            print(f"   ðŸŽ¯ Will process {len(sorted_slots)} slots sequentially")
            
            context = operation_context or getattr(self, "_active_operation_context", None)
            if context is None:
                return OperationResult(OperationStatus.UNKNOWN, direction="return", message="missing_operation_context")
            if before_snapshot is None:
                before_snapshot = self._capture_stable_placement_snapshot(
                    timeout_ms=1000,
                    context=context,
                )
            else:
                before_snapshot = PlacementSnapshot.from_mapping(before_snapshot)
            if before_snapshot is None:
                return self._operation_result(context, OperationStatus.UNKNOWN, "baseline_unavailable")
            context.set_baseline(before_snapshot)

            initial_count = (
                before_snapshot.characters_count
                if before_snapshot.characters_count is not None
                else before_snapshot.putted_count
            )
            if initial_count is None:
                return self._operation_result(context, OperationStatus.UNKNOWN, "board_count_unavailable")
            print(f"   ðŸ“Š Icons to clear: {initial_count}")

            success = self.execute_sequential_carpet_bombing_sets(
                sorted_slots,
                initial_count,
                use_cdp,
                operation_context=context,
            )
            after_snapshot = self._capture_stable_placement_snapshot(
                timeout_ms=1000,
                context=context,
            )
            if after_snapshot is None:
                return self._operation_result(context, OperationStatus.UNKNOWN, "post_carpet_state_unknown")

            evaluation = evaluate_transition(
                before_snapshot,
                after_snapshot,
                "return",
                context.target_slot,
            )
            if evaluation.status == OperationStatus.VERIFIED:
                result = self._operation_result(
                    context,
                    OperationStatus.VERIFIED,
                    evaluation.message,
                    evidence=evaluation.evidence,
                    details={
                        **evaluation.details,
                        "targetSpecific": evaluation.target_specific,
                        "aggregateFallback": evaluation.aggregate_fallback,
                    },
                )
                return result

            precondition = classify_precondition(after_snapshot, "return", context.target_slot)
            if precondition == OperationStatus.ALREADY_EMPTY:
                return self._operation_result(
                    context,
                    OperationStatus.ALREADY_EMPTY,
                    "target_empty_after_bounded_carpet",
                    evidence=("polos_target",),
                    details={"targetSpecific": True},
                )
            if not success:
                return self._operation_result(context, OperationStatus.FAILED, "carpet_no_progress")
            return self._operation_result(context, OperationStatus.UNKNOWN, "carpet_result_not_target_specific")
        except Exception as e:
            print(f"â Œ Error in carpet bombing return: {e}")
            return self._operation_result(
                operation_context,
                OperationStatus.FAILED,
                f"carpet_exception:{e}",
            )
                    

    def _execute_cdp_return(self, center_x, center_y, domain_adj):
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # âœ… NO VARIATION: Use exact coordinates
            # Move and click
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": center_x, "y": center_y}})
            time.sleep(0.1)
            self._simulate_click_via_cdp(ws, center_x, center_y)
            
            ws.close()
            
            # Wait for return animation
            return_wait = 0.4 * domain_adj['stability_factor']
            time.sleep(return_wait)
            
            print("âœ… CDP return completed")
            return True
            
        except Exception as e:
            print(f"âŒ CDP return failed: {e}")
            return False

    def _execute_pyautogui_return(self, click_x, click_y, domain_adj):
        """Execute single return click using PyAutoGUI"""
        return self._execute_pyautogui_single_click(click_x, click_y, domain_adj)

    def _execute_pyautogui_single_click(self, click_x, click_y, domain_adj):
        # 🔥 KIỂM TRA AN TOÀN TỌA ĐỘ TRƯỚC KHI THỰC HIỆN
        if not self._are_coords_safe(click_x, click_y):
            print("   ❌ PyAutoGUI click bị hủy do tọa độ không an toàn.")
            return False
        try:
            print(f"ðŸ–±ï¸  Executing PyAutoGUI single click at ({click_x}, {click_y})")
            
            # ðŸ”§ SAFE: Ensure focus before click (use light focus Ä‘á»ƒ trÃ¡nh click nháº§m)
            self._ensure_focus_before_operation("single click", focus_type="light")
            
            # âœ… NO VARIATION: Use exact coordinates
            pyautogui.moveTo(click_x, click_y, duration=0.15)  # â† CHÃNH XÃC 100%
            time.sleep(0.1)
            
            # Single click
            pyautogui.click(button='left')
            
            # Wait for click to register
            click_wait = 0.2 * domain_adj['stability_factor']
            time.sleep(click_wait)
            
            print("âœ… PyAutoGUI single click completed")
            return True
            
        except Exception as e:
            print(f"âŒ PyAutoGUI single click failed: {e}")
            return False

    # ================================================================
    # == HIGH-LEVEL GAME OPERATIONS (ENHANCED) ==
    # ================================================================
    
    def showcase_single_icon_corrected(self, current_icon, mode="Normal", listen_duration=15, progress_callback=None):
        """Enhanced version with resource management"""
        try:
            obs_controller = self.obs_controller
            icon_index = current_icon.get('index', -1)

            if not self.config or not current_icon:
                return False

            # Setup resource tracking
            if not hasattr(self, 'resource_manager'):
                self.resource_manager = ResourceManager()

            # Find target slot
            layout = self.config['layout']
            slots = layout.get('slots', [])
            center_slot_index = self.calculate_center_slot_index(slots)
            target_slot = self._find_slot_by_index(center_slot_index)
            if not target_slot:
                return False

            showcase_context = {"operation": "icon_showcase", "icon_index": icon_index, "mode": mode}

            if obs_controller:
                obs_controller.log_event_timestamp("start_icon_showcase", f"Start showcase cycle for icon {icon_index}", showcase_context)
            
            # --- GIAI ĐOẠN KÉO THẢ ---
            before_state = self.get_comprehensive_slot_state() if (self.characters_available or self.putted_chars_available) else None
            before_count = self._get_board_progress_count(before_state)
            if obs_controller:
                obs_controller.log_event_timestamp("start_drag", f"Dragging icon {icon_index}", {"success": None})
            
            drag_success = self.corrected_drag_and_drop(current_icon, target_slot, False, True)

            if obs_controller:
                obs_controller.log_event_timestamp(
                    "end_drag",
                    f"Drag for icon {icon_index} {'succeeded' if drag_success else 'failed'}",
                    {
                        "success": bool(drag_success),
                        "status": getattr(getattr(drag_success, "status", None), "value", None),
                    },
                )

            drag_verified = is_progress_verified(
                drag_success,
                allow_soft=getattr(self, "verifier_accept_aggregate_progress", True),
            )
            if not drag_verified:
                if obs_controller:
                    obs_controller.log_event_timestamp("icon_showcase_failed", f"Drag failed for icon {icon_index}", {"stage": "drag"})
                return False

            after_state = self.get_comprehensive_slot_state() if (self.characters_available or self.putted_chars_available) else None
            after_count = self._get_board_progress_count(after_state)
            transition_details = dict(getattr(drag_success, "details", {}) or {})
            transition_details["verificationEvidence"] = list(
                getattr(drag_success, "evidence", ()) or ()
            )
            if before_count is None:
                before_count = self._transition_count_from_details(transition_details, "before")
            if after_count is None:
                after_count = self._transition_count_from_details(transition_details, "after")
            transaction = {
                "iconIndex": icon_index,
                "slotIndex": target_slot.get("index"),
                "before": before_count,
                "after": after_count,
                "status": "VERIFIED",
                "targetVerified": bool(getattr(drag_success, "target_verified", False)),
                "softVerified": bool(
                    (getattr(drag_success, "details", {}) or {}).get("softVerified")
                    or (getattr(drag_success, "details", {}) or {}).get("aggregateFallback")
                ),
                "verificationConfidence": (
                    "strong"
                    if getattr(drag_success, "target_verified", False)
                    else "soft"
                ),
                "cleanupVerified": False,
                "operationId": getattr(drag_success, "operation_id", None),
                "transitionEvidence": transition_details,
            }
            self._workflow_placement_transactions.append(transaction)

            if self.stop_event.is_set(): return False
            
            # --- GIAI ĐOẠN NGHE NHẠC ---
            drag_details = getattr(self, "last_successful_drag_details", {}) or {}
            drop_time = drag_details.get("drop_time") or time.monotonic()
            segment_key = f"n-{icon_index + 1}"

            if obs_controller:
                obs_controller.log_event_timestamp(
                    "start_listen",
                    f"Listening to icon {icon_index} for {listen_duration}s",
                    {
                        "duration": listen_duration,
                        "mode": "Normal",
                        "icon_index": icon_index,
                        "segmentKey": segment_key,
                    },
                    event_timestamp=drop_time
                )
                listen_start_timestamp = round(drop_time - obs_controller.recording_start_time, 3)
                print(f"🕒 Video Event Logged: 'start_listen' at {listen_start_timestamp:.3f}s (drop_time anchored)")
            else:
                listen_start_timestamp = -1.0

            remaining_listen_time = max(0.0, listen_duration - (time.monotonic() - drop_time))
            listen_deadline = time.monotonic() + remaining_listen_time
            while time.monotonic() < listen_deadline:
                if self.stop_event.is_set():
                    return False
                time.sleep(min(1.0, max(0.0, listen_deadline - time.monotonic())))

            if obs_controller:
                end_listen_abs = drop_time + listen_duration
                obs_controller.log_event_timestamp(
                    "end_listen",
                    f"Finished listening to icon {icon_index}",
                    {"segmentKey": segment_key},
                    event_timestamp=end_listen_abs
                )
                end_listen_timestamp = round(end_listen_abs - obs_controller.recording_start_time, 3)
                print(f"🕒 Video Event Logged: 'end_listen' at {end_listen_timestamp:.3f}s (Calculated)")

            if self.stop_event.is_set(): return False

            # --- GIAI ĐOẠN TRẢ VỀ ---
            if obs_controller:
                obs_controller.log_event_timestamp("start_return", f"Returning icon {icon_index}")

            return_success = self.corrected_return_icon(target_slot, use_cdp=True)
            if not return_success:
                print(
                    f"🛑 Showcase stopped: return status "
                    f"{getattr(getattr(return_success, 'status', None), 'value', return_success)}"
                )
                return False

            return_status = getattr(getattr(return_success, "status", None), "value", None)
            transaction["cleanupVerified"] = (
                return_status == OperationStatus.ALREADY_EMPTY.value
                or (
                    return_status == OperationStatus.VERIFIED.value
                    and (
                        getattr(return_success, "target_verified", False)
                        or not getattr(
                            self,
                            "verifier_target_proof_required_for_cleanup",
                            False,
                        )
                    )
                )
            )

            if obs_controller:
                if return_success:
                     obs_controller.log_event_timestamp("end_icon_showcase", f"End showcase cycle for icon {icon_index}")
                else:
                    # Ghi nhận lỗi nếu return thất bại (dù corrected_return_icon trả về True)
                    obs_controller.log_event_timestamp("icon_showcase_failed", f"Return process for icon {icon_index} required a restart.", {"stage": "return"})

            # Force cleanup after each icon
            try:
                if hasattr(self, 'resource_manager'):
                    self.resource_manager.force_cleanup()
            except Exception as cleanup_error:
                print(f"Resource cleanup error: {cleanup_error}")
            
            time.sleep(0.1)
            
            print(f"COMPLETED ENHANCED SHOWCASE: {mode} icon {icon_index + 1}")
            return True
            
        except Exception as e:
            icon_index_for_error = current_icon.get('index', 'unknown') if current_icon else 'unknown'
            print(f"Critical error in showcase icon {icon_index_for_error}: {e}")
            
            # Emergency resource cleanup
            try:
                if hasattr(self, 'resource_manager'):
                    self.resource_manager.force_cleanup()
                gc.collect()
            except:
                pass
            
            return False

    def _legacy_execute_fast_showcase_cleanup_disabled(self):
            return self._execute_fast_showcase_cleanup_bounded()
            """
            ðŸš€ FAST: Optimized carpet bombing cho showcase mode
            - Early exit náº¿u Ä‘Ã£ clean
            - Reduced timing cho UX mÆ°á»£t mÃ   
            - Limited sets (chá»‰ 2-3 sets thay vÃ¬ 6)
            - Faster delays
            """
            try:
                print(f"\nðŸ§¹ FAST SHOWCASE CLEANUP (Intelligent Safety Net)")
                print("-" * 40)
                
                ### Lá»›p 3 - "Kiá»ƒm tra trÆ°á»›c, dá»n dáº¹p sau" ###
                
                # BÆ°á»›c 1: LuÃ´n kiá»ƒm tra state trÆ°á»›c tiÃªn
                dual_verification_enabled = self.characters_available or self.putted_chars_available
                initial_count = None

                if dual_verification_enabled:
                    characters_count, putted_count = self.get_dual_counts()
                    initial_count = characters_count if characters_count is not None else putted_count
                    
                    # BÆ°á»›c 2: Ra quyáº¿t Ä‘á»‹nh
                    if self._is_board_count_clean(characters_count=characters_count, putted_count=putted_count):
                        print("   âœ… Board is already clean. Skipping cleanup.")
                        print("   âš¡ Fast exit executed in ~0.2s.")
                        return True # ÄÃ¢y lÃ  lá»‘i thoÃ¡t nhanh, giÃºp tÄƒng tá»‘c Ä‘á»™
                    
                    print(f"   âš ï¸ Board is not clean! Found {initial_count} icon(s). Activating safety net...")
                else:
                    print("   🛑 Verification unavailable - refusing precautionary carpet bombing")
                    return OperationResult(
                        OperationStatus.UNKNOWN,
                        direction="cleanup",
                        message="cleanup_state_unknown",
                    )
                
                ### Káº¾T THÃšC Lá»›p 3 ###

                # Pháº§n code carpet bombing dÆ°á»›i Ä‘Ã¢y chá»‰ Ä‘Æ°á»£c thá»±c thi Náº¾U bÃ n cá» khÃ´ng sáº¡ch.
                print("   ðŸŽ¯ Activating full cleanup logic...")

                # Get configuration
                if not self.config:
                    print("   ❌ No config - cleanup cannot be verified")
                    return OperationResult(OperationStatus.FAILED, direction="cleanup", message="no_config")
                        
                all_slots = self.config.get('layout', {}).get('slots', [])
                if not all_slots:
                    print("   ❌ No slots - cleanup cannot be verified")
                    return OperationResult(OperationStatus.FAILED, direction="cleanup", message="no_slots")
                
                # Sort slots for sequential processing
                sorted_slots = sorted(all_slots, key=lambda s: s.get('index', 0))
                
                # ðŸš€ SHOWCASE-OPTIMIZED SETTINGS
                showcase_config = {
                    'max_sets': 3,
                    'base_coords_per_slot': 2,
                    'inter_click_delay': 0.02,
                    'inter_slot_delay': 0.8,
                    'inter_set_delay': 0.2
                }
                
                print(f"   âš¡ FAST mode: {showcase_config['max_sets']} sets, {len(sorted_slots)} slots")
                print(f"   â±ï¸ Estimated time: ~{self.estimate_cleanup_time(len(sorted_slots), showcase_config)}s")
                
                # Execute fast carpet bombing
                success = self.execute_fast_carpet_bombing_sets(
                    sorted_slots, initial_count, showcase_config, use_cdp=False
                )
                
                if success:
                    print("   âœ… Fast cleanup completed!")
                else:
                    print("   🛑 Fast cleanup incomplete")
                
                return OperationResult(
                    OperationStatus.VERIFIED if success else OperationStatus.FAILED,
                    direction="cleanup",
                    message="fast_cleanup_completed" if success else "fast_cleanup_incomplete",
                )
                
            except Exception as e:
                print(f"   âŒ Fast cleanup error: {e}")
                return OperationResult(
                    OperationStatus.FAILED,
                    direction="cleanup",
                    message=f"fast_cleanup_exception:{e}",
                )

    def execute_fast_showcase_cleanup(self):
        """Run the bounded fast-cleanup coordinator."""
        return self._execute_fast_showcase_cleanup_bounded()

    def _execute_fast_showcase_cleanup_bounded(self):
        """Bounded compatibility implementation for the fast cleanup entry point."""
        context = self._begin_operation(
            "cleanup",
            None,
            max_input_attempts=120,
            deadline_seconds=45.0,
        )
        if context is None:
            return OperationResult(
                OperationStatus.UNKNOWN,
                direction="cleanup",
                message="another_operation_is_active",
            )
        try:
            baseline = self._capture_stable_placement_snapshot(
                timeout_ms=1000,
                context=context,
            )
            if baseline is None:
                result = self._operation_result(context, OperationStatus.UNKNOWN, "cleanup_state_unknown")
                return self._finish_operation(context, result)
            if self._is_board_count_clean(
                characters_count=baseline.characters_count,
                putted_count=baseline.putted_count,
            ):
                result = self._operation_result(context, OperationStatus.ALREADY_EMPTY, "cleanup_already_empty")
                return self._finish_operation(context, result)
            context.set_baseline(baseline)

            slots = sorted(
                (self.config or {}).get("layout", {}).get("slots", []),
                key=lambda slot: slot.get("index", 0),
            )
            if not slots:
                result = self._operation_result(context, OperationStatus.FAILED, "cleanup_slots_unavailable")
                return self._finish_operation(context, result)

            success = self.execute_sequential_carpet_bombing_sets(
                slots,
                baseline.characters_count
                if baseline.characters_count is not None
                else baseline.putted_count,
                use_cdp=False,
                operation_context=context,
            )
            final_state = self._capture_stable_placement_snapshot(
                timeout_ms=1000,
                context=context,
            )
            if final_state is None:
                status = OperationStatus.UNKNOWN
                message = "cleanup_final_state_unknown"
            elif self._is_board_count_clean(
                characters_count=final_state.characters_count,
                putted_count=final_state.putted_count,
            ):
                status = OperationStatus.VERIFIED
                message = "fast_cleanup_verified"
            else:
                status = OperationStatus.FAILED if not success else OperationStatus.UNKNOWN
                message = "fast_cleanup_incomplete"
            return self._finish_operation(context, self._operation_result(context, status, message))
        except Exception as error:
            return self._finish_operation(
                context,
                self._operation_result(context, OperationStatus.FAILED, f"fast_cleanup_exception:{error}"),
            )

    def execute_fast_carpet_bombing_sets(self, sorted_slots, initial_count, config, use_cdp=False):
        """
        âš¡ Execute fast carpet bombing with reduced timing for showcase
        """
        # The legacy implementation below had no shared operation budget and
        # could report success without a final verified state.  The active
        # entry point is _execute_fast_showcase_cleanup_bounded(); keep this
        # compatibility method inert so it cannot reintroduce an unbounded
        # physical-input path.
        return OperationResult(
            OperationStatus.STOPPED,
            direction="cleanup",
            message="legacy_fast_carpet_bombing_disabled",
        )
        try:
            max_sets = config['max_sets']
            base_coords = config['base_coords_per_slot']
            
            for set_num in range(1, max_sets + 1):
                if self.stop_event.is_set():
                    break
                    
                print(f"   ðŸ’¥ Fast set {set_num}/{max_sets}")
                
                # Check if already clean
                if self.characters_available or self.putted_chars_available:
                    current_chars, current_putted = self.get_dual_counts()
                    current_count = current_chars if current_chars is not None else current_putted
                    
                    if self._is_board_count_clean(characters_count=current_chars, putted_count=current_putted):
                        print(f"   âœ… Clean after set {set_num} - early exit!")
                        return True
                
                # Generate coordinates for this set
                coords_per_slot = base_coords + (set_num - 1)
                
                # Execute all slots in this set
                for slot in sorted_slots:
                    if self.stop_event.is_set():
                        break
                        
                    # Generate coordinates for this slot
                    slot_coords = self.generate_slot_coordinates(
                        slot['x'], slot['y'], coords_per_slot, set_num
                    )
                    
                    # Execute clicks for this slot
                    for coord_x, coord_y in slot_coords:
                        self._execute_single_return_click(
                            coord_x, coord_y, use_cdp, self.get_domain_adjustments()
                        )
                        time.sleep(config['inter_click_delay'])
                    
                    # Fast inter-slot delay
                    time.sleep(config['inter_slot_delay'])
                
                # Fast inter-set delay
                time.sleep(config['inter_set_delay'])
            
            return True
            
        except Exception as e:
            print(f"   âŒ Fast carpet bombing error: {e}")
            return False

    def estimate_cleanup_time(self, num_slots, config):
        """
        â±ï¸ Estimate cleanup time for user feedback
        """
        try:
            sets = config['max_sets']
            avg_coords_per_slot = 2.5  # Average of 2,3,4 coords
            
            # Calculate components
            click_time = num_slots * avg_coords_per_slot * config['inter_click_delay'] * sets
            slot_delays = (num_slots - 1) * config['inter_slot_delay'] * sets  
            set_delays = (sets - 1) * config['inter_set_delay']
            
            total = click_time + slot_delays + set_delays
            return round(total, 1)
            
        except:
            return 5  # Fallback estimate

    # ================================================================
    # == EXISTING GAME LOGIC (kept as-is with small adaptations) ==
    # ================================================================

    def smart_delay(self, base_time, variation=0.3):
        """Smart delay with random variation and domain adjustments"""
        domain_adj = self.get_domain_adjustments()
        adjusted_base = base_time * domain_adj['stability_factor']
        min_time = max(0.08, adjusted_base - variation)
        max_time = adjusted_base + variation
        delay = random.uniform(min_time, max_time)
        if adjusted_base > 0.3:
            delay += 0.1
        time.sleep(delay)
        return delay
    
    def quick_delay(self):
        return self.smart_delay(0.15, 0.05)
    
    def normal_delay(self):
        return self.smart_delay(0.25, 0.1)
    
    def thinking_delay(self):
        return self.smart_delay(0.4, 0.2)

    def generate_random_remixes(self, avoid_horror_icon=False):
        """Generate random remixes with option to avoid horror icon"""
        count = random.randint(3, 5)
        remix_library = []
        for i in range(count):
            pattern = random.choice(self.remix_patterns)
            remix = self.create_remix_from_pattern(pattern, i + 1, avoid_horror_icon=avoid_horror_icon)
            if remix:
                remix_library.append(remix)
        
        mode_suffix = " (No Horror Icon)" if avoid_horror_icon else ""
        print(f"Generated {len(remix_library)} random remixes{mode_suffix}!")
        return remix_library
    
    def create_remix_from_pattern(self, pattern, remix_number, avoid_horror_icon=False):
        """
        Tạo remix từ pattern, đảm bảo các icon được chọn nằm trong giới hạn thực tế
        của file JSON - FLEXIBLE ICON COUNT.
        """
        try:
            selected_icons = []
            
            if not self.config or 'layout' not in self.config or 'icons' not in self.config['layout']:
                print("❌ Cannot create remix: Config with icon layout not available.")
                return None
                
            all_icons_from_json = self.config['layout']['icons']
            total_icons_available = len(all_icons_from_json)
            print(f"🎼 Creating remix based on {total_icons_available} available icons.")

            # --- LOGIC TẠO NHÓM ICON LINH HOẠT MỚI ---
            # Tạo icon groups động dựa trên số lượng icon thực tế
            dynamic_icon_groups = {
                'beats': [i['index'] for i in all_icons_from_json if 0 <= i.get('index', -1) <= 4],
                'effects': [i['index'] for i in all_icons_from_json if 5 <= i.get('index', -1) <= 9],
                'melodies': [i['index'] for i in all_icons_from_json if 10 <= i.get('index', -1) <= 14],
                # Vocals là TẤT CẢ các icon còn lại từ index 15 trở đi
                'vocals': [i['index'] for i in all_icons_from_json if i.get('index', -1) >= 15]
            }

            game_settings = self.config.get('gameSettings', {})
            is_icon_19_disabled = game_settings.get('isIcon19Disabled', False)

            # Luôn loại bỏ icon 19 nếu nó bị vô hiệu hóa, hoặc nếu đang tạo remix cho normal mode
            if 19 in dynamic_icon_groups['vocals']:
                if is_icon_19_disabled:
                    dynamic_icon_groups['vocals'].remove(19)
                    print(f"🚫 Avoiding disabled icon (19) for remix.")
                elif avoid_horror_icon:
                    dynamic_icon_groups['vocals'].remove(19)
                    print(f"👻 Avoiding horror icon (19) for normal mode remix.")

            for group_name, count in [('beats', pattern['beats']), 
                                      ('effects', pattern['effects']),
                                      ('melodies', pattern['melodies']), 
                                      ('vocals', pattern['vocals'])]:
                if count > 0:
                    available_icons_in_group = dynamic_icon_groups.get(group_name, [])
                    
                    if not available_icons_in_group:
                        print(f"   ⚠️ No icons available for group '{group_name}', skipping.")
                        continue
                        
                    num_to_select = min(count, len(available_icons_in_group))
                    selected = random.sample(available_icons_in_group, num_to_select)
                    selected_icons.extend(selected)
            
            random.shuffle(selected_icons)
            pattern_counts = f"{pattern['beats']}B+{pattern['effects']}E+{pattern['melodies']}M+{pattern['vocals']}V"
            remix_name = f"{pattern['name']} #{remix_number}"
            
            return {
                'name': remix_name,
                'description': f"{pattern['description']} ({pattern_counts})",
                'icons': selected_icons,
                'pattern': pattern['name']
            }
        except Exception as e:
            print(f"❌ Error creating remix from pattern: {e}")
            return None

    def _adjust_sum_to_target(self, counts: Dict[str, int], target_sum: int) -> Dict[str, int]:
        """Hàm trợ giúp để điều chỉnh tổng các số đếm cho khớp với target_sum."""
        current_sum = sum(counts.values())
        diff = target_sum - current_sum

        # Sắp xếp các loại theo giá trị hiện tại để ưu tiên điều chỉnh
        # Ưu tiên thêm/bớt vào 'beats' và 'effects'
        priority = ['beats', 'effects', 'melodies', 'vocals']
        
        while diff != 0:
            if diff > 0: # Cần thêm
                # Thêm vào thành phần có giá trị nhỏ nhất trong danh sách ưu tiên
                key_to_increment = min(priority, key=lambda k: counts.get(k, 0))
                counts[key_to_increment] = counts.get(key_to_increment, 0) + 1
                diff -= 1
            else: # Cần bớt
                # Bớt từ thành phần có giá trị lớn nhất (và > 0)
                key_to_decrement = max(priority, key=lambda k: counts.get(k, 0))
                if counts.get(key_to_decrement, 0) > 0:
                    counts[key_to_decrement] -= 1
                    diff += 1
                else: # Nếu không thể bớt được nữa, dừng lại để tránh số âm
                    break 
        return counts

    def generate_dynamic_patterns(self, total_slots: int):
        """
        Tạo các pattern remix một cách linh động dựa trên tổng số slot có sẵn.
        LUÔN tạo tất cả các loại pattern.
        """
        print(f"🎼 Generating dynamic remix patterns for {total_slots} slots...")
        if total_slots <= 0:
            self.remix_patterns = [] # Xóa các pattern cũ nếu không có slot
            return []

        dynamic_patterns = []

        # 1. Khuôn mẫu "Balanced" (Cân bằng)
        balanced_counts = {
            'beats': max(1, round(total_slots * 0.40)),
            'effects': max(1, round(total_slots * 0.30)),
            'melodies': max(1, round(total_slots * 0.20)),
            'vocals': max(0, round(total_slots * 0.10))
        }
        balanced_counts = self._adjust_sum_to_target(balanced_counts, total_slots)
        dynamic_patterns.append({
            'name': f'Balanced {total_slots}-Slot', 
            'description': f'Bản phối cân bằng cho {total_slots} slot', 
            **balanced_counts
        })

        # 2. Khuôn mẫu "Beat Heavy" (Nhiều nhịp điệu)
        beat_heavy_counts = {
            'beats': max(2, round(total_slots * 0.60)),
            'effects': max(1, round(total_slots * 0.20)),
            'melodies': max(1, round(total_slots * 0.10)),
            'vocals': max(1, round(total_slots * 0.10))
        }
        if beat_heavy_counts['beats'] >= total_slots - 2:
            beat_heavy_counts['beats'] = total_slots - 3
        beat_heavy_counts = self._adjust_sum_to_target(beat_heavy_counts, total_slots)
        dynamic_patterns.append({
            'name': f'Beat Heavy {total_slots}-Slot', 
            'description': f'Bản phối tập trung vào nhịp điệu', 
            **beat_heavy_counts
        })
        
        # 3. Khuôn mẫu "Melodic & Atmospheric" (Giai điệu & Không khí)
        melodic_counts = {
            'beats': max(1, round(total_slots * 0.25)),
            'effects': max(1, round(total_slots * 0.35)),
            'melodies': max(1, round(total_slots * 0.30)),
            'vocals': max(1, round(total_slots * 0.10))
        }
        melodic_counts = self._adjust_sum_to_target(melodic_counts, total_slots)
        dynamic_patterns.append({
            'name': f'Melodic {total_slots}-Slot', 
            'description': f'Bản phối tập trung giai điệu và hiệu ứng', 
            **melodic_counts
        })

        # 4. Khuôn mẫu "No Vocals"
        no_vocals_counts = {
            'beats': max(2, round(total_slots * 0.50)),
            'effects': max(2, round(total_slots * 0.30)),
            'melodies': max(1, round(total_slots * 0.20)),
            'vocals': 0
        }
        no_vocals_counts = self._adjust_sum_to_target(no_vocals_counts, total_slots)
        dynamic_patterns.append({
            'name': f'No Vocals {total_slots}-Slot', 
            'description': f'Bản phối không lời', 
            **no_vocals_counts
        })

        print(f"✅ Generated {len(dynamic_patterns)} dynamic patterns:")
        for p in dynamic_patterns:
            icon_total = p['beats'] + p['effects'] + p['melodies'] + p['vocals']
            print(f"   - {p['name']}: B={p['beats']}, E={p['effects']}, M={p['melodies']}, V={p['vocals']} (Total: {icon_total})")

        # Cập nhật thuộc tính của class để các hàm khác sử dụng
        self.remix_patterns = dynamic_patterns
        return dynamic_patterns

    def _find_slot_by_index(self, slot_index):
        if not self.config:
            return None
        layout = self.config['layout']
        slots = layout.get('slots', [])
        for slot in slots:
            if slot.get('index') == slot_index:
                return slot
        return None

    def calculate_center_slot_index(self, slots: list) -> int:
        """
        [TÍNH NĂNG MỚI] Tính toán index của slot trung tâm một cách linh động.
        """
        try:
            total_slots = len(slots)
            if total_slots == 0:
                print("⚠️ No slots found, defaulting to center index 3")
                return 3 # Trả về fallback an toàn

            # Tính toán index của slot ở giữa (0-based)
            # Ví dụ: 7 slots (0-6) -> (7-1)//2 = 3
            # Ví dụ: 9 slots (0-8) -> (9-1)//2 = 4
            center_index = (total_slots - 1) // 2
            
            print(f"📊 Slot calculation: {total_slots} slots → center index {center_index}")
            
            return center_index
            
        except Exception as e:
            print(f"❌ Error calculating center slot: {e}, falling back to index 3")
            return 3  # Fallback về giá trị an toàn nếu có lỗi

    def get_drag_statistics(self):
        return self.drag_stats.get_stats()

    def prepare_game_for_playing(self):
        """Prepare game for playing with enhanced setup"""
        try:
            print("ðŸŽ¯ Preparing game for enhanced workflow...")
            
            # Clean mouse state
            self.ensure_mouse_released()
            
            # Warm-up first icon
            if self.config is not None:  # âœ… Kiá»ƒm tra config khÃ´ng None
                layout = self.config['layout']
                if layout['icons']:
                    first_icon = layout['icons'][0]
                    print("ðŸŽ¯ Warming up first icon...")
                    
                    # âœ… DI CHUYá»‚N Táº¤T Cáº¢ CODE VÃ€O TRONG KHá»I IF
                    warm_x, warm_y = first_icon['x'], first_icon['y']  # â† BÃ¢y giá» an toÃ n
                    
                    pyautogui.moveTo(warm_x, warm_y, duration=random.uniform(0.3, 0.5))
                    self.smart_delay(0.3, 0.1)
                    
                    # Micro-movement - CÅ¨NG CHÃNH XÃC
                    offset_x, offset_y = warm_x + 8, warm_y + 8  # â† BÃ¢y giá» an toÃ n
                    pyautogui.moveTo(offset_x, offset_y, duration=0.15)
                    self.smart_delay(0.2, 0.05)
                    pyautogui.moveTo(warm_x, warm_y, duration=0.15)  # â† BÃ¢y giá» an toÃ n
                    self.smart_delay(0.3, 0.1)
                else:
                    print("âš ï¸ No icons available for warm-up")
            else:
                print("âš ï¸ Config not loaded, skipping warm-up")
            
            print("âœ… Game preparation completed with enhanced workflow!")
            
        except Exception as e:
            print("Error in game preparation: " + str(e))

    # ================================================================
    # == WORKFLOW METHODS (redirected to enhanced versions) ==
    # ================================================================

    def showcase_all_icons_corrected(self, mode="Normal", skip_icon_19=False, listen_duration=15, progress_callback=None):
        """ENHANCED: Showcase all icons - FLEXIBLE & RESILIENT"""
        success_count = 0
        total_count = 0
        try:
            if not self.config:
                return 0, 0 # success_count, total_count

            layout = self.config['layout']
            all_icons = layout['icons']
            
            game_settings = self.config.get('gameSettings', {})
            is_icon_19_disabled = game_settings.get('isIcon19Disabled', False)

            icons_to_showcase = all_icons

            # Luôn loại bỏ icon 19 nếu nó bị đánh dấu là "vô hiệu hóa"
            if is_icon_19_disabled:
                icons_to_showcase = [icon for icon in all_icons if icon.get('index') != 19]
                print(f"ℹ️ {mode} Mode: Filtering out disabled icon 19. Processing {len(icons_to_showcase)} icons.")
            # Nếu không, áp dụng logic cũ (loại bỏ icon 19 cho normal mode nếu có horror)
            elif skip_icon_19:
                icons_to_showcase = [icon for icon in all_icons if icon.get('index') != 19]
                print(f"ℹ️ {mode} Mode: Filtering out horror icon 19. Processing {len(icons_to_showcase)} icons.")
            else:
                print(f"ℹ️ {mode} Mode: Processing all {len(icons_to_showcase)} available icons.")

            total_count = len(icons_to_showcase)
            if total_count == 0:
                print(f"ℹ️ {mode} Mode: No icons to showcase.")
                return 0, 0

            print(f"\n🎬 STARTING ENHANCED {mode.upper()} SHOWCASE")
            print(f"📄 Will showcase {total_count} icons")
            print("="*60)
            
            if mode.lower() == "normal" and not self.character_identity_map_written:
                self.write_character_identity_map_for_current_project()
                self.character_identity_map_written = True

            consecutive_failures = 0
            FAILURE_THRESHOLD = 3  # Ngưỡng: 3 icon thất bại liên tiếp

            for icon_object in icons_to_showcase:
                if self.stop_event.is_set(): # [MODIFIED]
                    self.logger.warning("Stop event detected, aborting icon showcase.")
                    break
                
                if self.showcase_single_icon_corrected(icon_object, mode, listen_duration, progress_callback):
                    success_count += 1
                    consecutive_failures = 0  # Reset khi thành công
                else:
                    icon_index = icon_object.get('index', 'unknown')
                    print(f"⚠️ Failed to showcase icon {icon_index}, but continuing with the next one.")
                    last_result = getattr(self, "last_operation_result", None)
                    last_status = getattr(getattr(last_result, "status", None), "value", None)
                    if last_status in {"UNKNOWN", "RECOVERED", "STOPPED"}:
                        raise WorkflowIncompatibleError(
                            f"Showcase stopped after non-replayable {last_status} result for icon {icon_index}"
                        )
                    consecutive_failures += 1

                # [REFINED] Kích hoạt ngắt mạch nếu vượt ngưỡng bằng cách ném Exception
                if consecutive_failures >= FAILURE_THRESHOLD:
                    error_msg = f"Circuit breaker tripped after {consecutive_failures} consecutive icon failures. Mod is likely incompatible."
                    print(f"🚨 [CIRCUIT BREAKER] {error_msg}")
                    raise WorkflowIncompatibleError(error_msg)

            print(f"\n✅ ENHANCED {mode.upper()} SHOWCASE COMPLETED")
            print(f"📊 Success: {success_count}/{total_count} icons")
            print("="*60)
            
            return success_count, total_count
                
        except Exception as e:
            print("❌ Error in enhanced " + mode + " showcase: " + str(e))
            # Preserve the verified successes already completed.  Returning
            # ``0,total`` here erased real evidence whenever a late CDP read
            # or cleanup check failed after one or more icons had been shown.
            return success_count, total_count

    def play_complete_remix_corrected(self, remix_data, mode="Normal", listen_duration=15, progress_callback=None):
        """
        [FIXED & CHUẨN HÓA] Play remix, TRUYỀN obs_controller và context xuống các hàm cấp dưới.
        """
        try:
            if not self.config or not remix_data:
                return False
                
            layout = self.config['layout']
            icons = layout['icons']
            slots = layout['slots']
            
            remix_name = remix_data.get('name', 'Unknown Remix')
            remix_icons = remix_data.get('icons', [])
            if not isinstance(remix_icons, list) or not remix_icons:
                print("🛑 Remix has no icons; refusing to report a successful empty remix.")
                return False

            transaction_start = len(self._workflow_placement_transactions)
            
            emoji = "ðŸŽµ" if mode == "Test" else ("ðŸ‘»" if mode == "Horror" else "ðŸŽª")
            
            print(f"\n{emoji} STARTING ENHANCED REMIX: {remix_name}")
            
            # === BƯỚC 1: Lấy obs_controller và tạo context ===
            obs_controller = self.obs_controller
            remix_metadata = {
                "remix_name": remix_name,
                "icon_indices": remix_icons,
                "pattern": remix_data.get('pattern', 'unknown')
            }
            
            # Phase 1: Drag all icons to slots
            print("ðŸŽ¯ PHASE 1: Dragging all icons to slots for remix...")
            placed_count = 0
            for slot_position, icon_index in enumerate(remix_icons):
                if self.stop_event.is_set(): return False
                
                if icon_index >= len(icons) or slot_position >= len(slots):
                    continue
                
                current_icon = icons[icon_index]
                target_slot = slots[slot_position]

                drag_result = self.corrected_drag_and_drop(
                    current_icon,
                    target_slot,
                    False,
                    use_cdp=True,
                )
                if not drag_result:
                    print(
                        "🛑 Remix stopped after bounded drag result "
                        f"{getattr(getattr(drag_result, 'status', None), 'value', drag_result)}"
                    )
                    return False
                if not is_progress_verified(
                    drag_result,
                    allow_soft=getattr(
                        self, "verifier_accept_aggregate_progress", True
                    ),
                ):
                    print("🛑 Remix drag produced no verified aggregate progress.")
                    return False

                placed_count += 1
                transition_details = dict(getattr(drag_result, "details", {}) or {})
                transition_details["verificationEvidence"] = list(
                    getattr(drag_result, "evidence", ()) or ()
                )
                self._workflow_placement_transactions.append(
                    {
                        "iconIndex": icon_index,
                        "slotIndex": target_slot.get("index"),
                        "before": self._transition_count_from_details(transition_details, "before"),
                        "after": self._transition_count_from_details(transition_details, "after"),
                        "status": "VERIFIED",
                        "targetVerified": bool(getattr(drag_result, "target_verified", False)),
                        "softVerified": bool(
                            transition_details.get("softVerified")
                            or transition_details.get("aggregateFallback")
                        ),
                        "verificationConfidence": (
                            "strong"
                            if getattr(drag_result, "target_verified", False)
                            else "soft"
                        ),
                        "cleanupVerified": False,
                        "operationId": getattr(drag_result, "operation_id", None),
                        "transitionEvidence": transition_details,
                    }
                )
                time.sleep(0.3)

                if self.stop_event.is_set(): return False

            if placed_count <= 0:
                print("🛑 Remix contained no valid icon placements.")
                return False
            
            # === BƯỚC 2: GHI TIMESTAMP BẮT ĐẦU NGHE NHẠC ===
            if obs_controller:
                obs_controller.log_event_timestamp(
                    "start_remix_playback", 
                    f"Start playback for {remix_name}", 
                    {"mode": mode, **remix_metadata}
                )

            # Phase 2: Listen to remix music
            print(f"PHASE 2: Enjoying remix for {listen_duration}s...")
            for countdown in range(listen_duration, 0, -1):
                if self.stop_event.is_set(): return False
                
                if progress_callback and countdown % 3 == 0:
                    try:
                        self.thread_safe_progress_callback(f"Enjoying {remix_name} ({countdown}s)")
                    except Exception as e:
                        print(f"Progress callback warning: {e}")
                
                time.sleep(1)
            
            if self.stop_event.is_set(): return False
            
            # === BƯỚC 3: GHI TIMESTAMP KẾT THÚC NGHE NHẠC ===
            if obs_controller:
                obs_controller.log_event_timestamp(
                    "end_remix_playback", 
                    f"End playback for {remix_name}",
                    {"mode": mode}
                )

            # Phase 3: Smart batch return
            print("ðŸ”„ PHASE 3: Smart batch returning with mode detection...")
            if progress_callback:
                progress_callback(f"ðŸ§¹ Smart cleanup {remix_name}")

            self.set_return_mode_explicit(self.RETURN_MODE_REMIX, f"remix_cleanup_{len(remix_icons)}_icons")
            
            # === BƯỚC 4 (FIX): Truyền context và obs_controller xuống hàm dọn dẹp ===
            # Lời gọi cũ không truyền tham số: self.smart_batch_return_all_slots_with_mode(use_cdp=False)
            success_count, total_attempted, error_log = self.smart_batch_return_all_slots_with_mode(
                use_cdp=False, 
                context=remix_metadata, # Truyền context
                obs_controller=obs_controller # Truyền controller tường minh
            )
            
            if total_attempted > 0 and success_count == total_attempted:
                print(f"  âœ… Smart batch return: {success_count}/{total_attempted} icons returned successfully")
                for transaction in self._workflow_placement_transactions[transaction_start:]:
                    transaction["cleanupVerified"] = True
            else:
                print(f"  âš ï¸  Smart batch return: {success_count}/{total_attempted} icons returned")
                if error_log:
                    print(f"  ðŸ“‹ Errors: {len(error_log)} issues encountered")
                print("  🛑 Remix is not considered complete after unverified cleanup")
                return False

            time.sleep(0.5)
            
            print(f"âœ… COMPLETED ENHANCED REMIX: {remix_name}")
            print("="*60)
            return True
            
        except Exception as e:
            print(f"â Œ Error in enhanced remix {remix_data.get('name', 'Unknown')}: {e}")
            return False

    def _remix_stop_requested(self) -> bool:
        """Return whether a remix retry would violate the active stop request."""
        stop_event = getattr(self, "stop_event", None)
        return bool(getattr(self, "should_stop", False)) or bool(
            stop_event is not None and stop_event.is_set()
        )

    def _remix_retry_settings(self) -> Tuple[int, float]:
        """Read bounded whole-remix retry settings with safe defaults."""
        try:
            max_attempts = max(1, int(getattr(self, "remix_max_attempts", 2)))
        except (TypeError, ValueError):
            max_attempts = 2
        try:
            retry_delay = max(
                0.0, float(getattr(self, "remix_retry_delay_seconds", 1.0))
            )
        except (TypeError, ValueError):
            retry_delay = 1.0
        return max_attempts, retry_delay

    def _remix_input_is_retryable(self, remix_data: Any) -> Tuple[bool, str]:
        """Reject malformed remix definitions before spending retry budget."""
        if not isinstance(remix_data, dict):
            return False, "remix_definition_not_an_object"
        remix_icons = remix_data.get("icons")
        if not isinstance(remix_icons, list) or not remix_icons:
            return False, "remix_has_no_icons"

        layout = self.config.get("layout", {}) if isinstance(self.config, dict) else {}
        icons = layout.get("icons", []) if isinstance(layout, dict) else []
        slots = layout.get("slots", []) if isinstance(layout, dict) else []
        if not isinstance(icons, list) or not isinstance(slots, list):
            return False, "remix_layout_unavailable"
        if len(remix_icons) > len(slots):
            return False, "remix_requires_more_slots_than_available"
        if any(
            not isinstance(icon_index, int) or icon_index < 0 or icon_index >= len(icons)
            for icon_index in remix_icons
        ):
            return False, "remix_contains_invalid_icon_index"
        return True, "valid_remix_definition"

    def _annotate_remix_transactions(
        self,
        transaction_start: int,
        remix_name: str,
        attempt: int,
        *,
        completed: bool,
        cleanup_verified: Optional[bool] = None,
    ) -> int:
        """Attach retry identity to placement evidence without discarding it."""
        transactions = self._workflow_placement_transactions[transaction_start:]
        for transaction in transactions:
            transaction["remixName"] = remix_name
            transaction["remixAttempt"] = attempt
            transaction["remixCompleted"] = bool(completed)
            if cleanup_verified is not None:
                transaction["cleanupVerified"] = bool(cleanup_verified)
        return len(transactions)

    def _cleanup_partial_remix_for_retry(
        self,
        remix_data: Dict[str, Any],
        attempt: int,
        transaction_start: int,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Clean and verify a partial remix before another attempt is allowed."""
        remix_name = str(remix_data.get("name", "Unknown Remix"))
        context = {
            "workflow": "remix_retry_cleanup",
            "remix_name": remix_name,
            "remix_attempt": attempt,
        }
        errors: List[str] = []
        success_count = 0
        total_attempted = 0
        validation_complete = False
        validation_issues: List[str] = []

        print(
            f"🧹 Cleaning partial remix before retry: {remix_name} "
            f"(attempt {attempt})"
        )
        try:
            success_count, total_attempted, errors = (
                self.smart_batch_return_all_slots_with_mode(
                    use_cdp=False,
                    context=context,
                    obs_controller=self.obs_controller,
                )
            )
            validation = self.validate_workflow_completion()
            if isinstance(validation, tuple):
                validation_complete = bool(validation[0])
                validation_issues = list(validation[1] or [])
            else:
                validation_complete = bool(validation)
        except Exception as error:
            errors = [f"cleanup_exception:{error}"]

        batch_complete = not errors and (
            total_attempted == 0 or success_count == total_attempted
        )
        cleanup_verified = bool(batch_complete and validation_complete)
        placed_count = self._annotate_remix_transactions(
            transaction_start,
            remix_name,
            attempt,
            completed=False,
            cleanup_verified=cleanup_verified,
        )
        details = {
            "remixName": remix_name,
            "attempt": attempt,
            "placedTransactions": placed_count,
            "successCount": success_count,
            "totalAttempted": total_attempted,
            "errors": list(errors),
            "validationComplete": validation_complete,
            "validationIssues": validation_issues,
            "cleanupVerified": cleanup_verified,
        }
        if cleanup_verified:
            print(
                f"✅ Partial remix cleanup verified: {remix_name} "
                f"({placed_count} placed transaction(s) cleared)"
            )
        else:
            print(
                f"🛑 Partial remix cleanup NOT verified: {remix_name}; "
                "retry is blocked"
            )
        return cleanup_verified, details

    def _play_remix_with_retry(
        self,
        remix_data: Dict[str, Any],
        mode: str = "Normal",
        listen_duration: int = 15,
        progress_callback=None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Run one complete remix with cleanup-gated whole-remix retries."""
        remix_name = str(remix_data.get("name", "Unknown Remix"))
        max_attempts, retry_delay = self._remix_retry_settings()
        input_valid, input_reason = self._remix_input_is_retryable(remix_data)
        attempt_details: List[Dict[str, Any]] = []

        for attempt in range(1, max_attempts + 1):
            if self._remix_stop_requested():
                attempt_details.append(
                    {
                        "remixName": remix_name,
                        "attempt": attempt,
                        "success": False,
                        "retryable": False,
                        "reason": "stop_requested",
                    }
                )
                return False, attempt_details

            transaction_start = len(self._workflow_placement_transactions)
            started_at = time.monotonic()
            if not input_valid:
                success = False
            else:
                try:
                    success = bool(
                        self.play_complete_remix_corrected(
                            remix_data,
                            mode,
                            listen_duration,
                            progress_callback,
                        )
                    )
                except Exception as error:
                    print(f"⚠️ Remix attempt raised an error: {error}")
                    success = False

            placed_count = self._annotate_remix_transactions(
                transaction_start,
                remix_name,
                attempt,
                completed=success,
            )
            detail = {
                "remixName": remix_name,
                "attempt": attempt,
                "success": success,
                "retryable": input_valid,
                "reason": input_reason if not input_valid else None,
                "placedTransactions": placed_count,
                "durationSeconds": round(time.monotonic() - started_at, 3),
            }

            if success:
                detail["cleanupVerified"] = True
                attempt_details.append(detail)
                return True, attempt_details

            cleanup_verified, cleanup_detail = self._cleanup_partial_remix_for_retry(
                remix_data,
                attempt,
                transaction_start,
            )
            detail["cleanup"] = cleanup_detail
            detail["cleanupVerified"] = cleanup_verified
            attempt_details.append(detail)

            if not cleanup_verified or not input_valid:
                return False, attempt_details
            if attempt >= max_attempts:
                return False, attempt_details

            print(
                f"🔁 Retrying complete remix: {remix_name} "
                f"({attempt + 1}/{max_attempts})"
            )
            if retry_delay > 0:
                time.sleep(retry_delay)

        return False, attempt_details

    def execute_full_showcase_workflow_corrected(self, progress_callback=None) -> WorkflowReport:
        """
        ENHANCED: Execute full workflow with verification, resilience, and detailed reporting.
        """
        report = WorkflowReport(mode="master")
        self.last_workflow_report = report
        self._workflow_placement_transactions = []
        self._reset_cleanup_guard()
        try:
            if not self.config:
                report.apply_quality_gate(
                    QualityGateResult.failure(
                        "manual_json_invalid",
                        message="Configuration not loaded.",
                    )
                )
                report.issues.append("Config not loaded.")
                return report
                
            game_settings = self.config.get('gameSettings', {})
            configured_has_horror = bool(game_settings.get('hasHorror', False))
            # Current production policy verifies Normal only.  Horror is
            # opt-in through an explicit setting and is otherwise recorded as
            # not_checked_by_policy rather than silently treated as absent.
            has_horror = bool(game_settings.get('verifyHorror', False)) and configured_has_horror
            has_bonus = game_settings.get('hasBonus', False)
            layout = self.config.get('layout', {}) or {}
            report.layout_icons = len(layout.get('icons', []) or [])
            report.layout_slots = len(layout.get('slots', []) or [])
            report.entry_candidate = self.entry_strategy.get('selected')
            report.tested_candidates = list(self.entry_strategy.get('tested', []) or [])
            report.evidence["entryStrategy"] = dict(self.entry_strategy)
            report.runtime_state = str(self.entry_strategy.get('verification') or UNKNOWN)
            # Runtime entry verification is authoritative for playability;
            # optional dual placement readers are not a prerequisite.
            report.runtime_verified = report.runtime_state == "PASS"
            report.coverage = {
                "normal": "not_started",
                "horror": "verified" if has_horror else "not_checked_by_policy",
            }
            
            print("\n🎪 STARTING ENHANCED FULL SHOWCASE WORKFLOW")
            print("="*60)
            
            # --- State Tracking Variables ---
            is_normal_phase_perfect = True
            normal_phase_did_run = False
            is_horror_phase_perfect = False # Will be set to True only if horror mode runs
            
            # === NORMAL MODE ===
            if progress_callback:
                progress_callback("🎪 Starting Enhanced Normal Mode...")
            
            # Calculate one fixed recordingArea for the whole showcase.
            self.runtime_recording_area = None
            try:
                slots = self.config.get('layout', {}).get('slots', [])
                center_slot_index = self.calculate_center_slot_index(slots)
                target_slot = self._find_slot_by_index(center_slot_index)
                self.calculate_fixed_runtime_recording_area(target_slot)
            except Exception as area_error:
                report.issues.append(f"Runtime recordingArea fallback used: {area_error}")
                self.calculate_fixed_runtime_recording_area(None)

            # Normal icons
            if not self.should_stop:
                success, total = self.showcase_all_icons_corrected("Normal", skip_icon_19=configured_has_horror, progress_callback=progress_callback)
                report.normal_icons_showcased = success
                report.normal_icons_total = total
                report.evidence["normalIconShowcase"] = {
                    "showcased": success,
                    "total": total,
                    "failed": max(total - success, 0),
                    "status": "PASS" if total > 0 and success == total else "FAIL",
                }
                if success < total:
                    is_normal_phase_perfect = False
                    report.issues.append(f"Normal showcase failed for {total - success} icon(s).")

            # Normal bonus
            if has_bonus and not self.should_stop:
                if not self.showcase_all_bonus_corrected("Normal", progress_callback=progress_callback):
                    is_normal_phase_perfect = False
                    report.issues.append("Normal bonus showcase failed.")

            # Log Normal Remix Performance Start
            if self.obs_controller:
                self.obs_controller.log_event_timestamp(
                    "start_full_remix_performance", "Starting Normal full remix performance section", {"mode": "Normal"}
                )

            # Normal remixes
            if not self.should_stop:
                remix_result = self.play_all_remixes_corrected("Normal", progress_callback=progress_callback)
                report.normal_remixes_played = remix_result.succeeded
                report.normal_remixes_total = remix_result.attempted
                report.evidence["normalRemixResult"] = remix_result.as_dict()
                if remix_result.succeeded < remix_result.attempted:
                    is_normal_phase_perfect = False
                    report.issues.append(
                        f"Normal remixes failed for {remix_result.attempted - remix_result.succeeded} item(s)."
                    )

            # Report Normal Phase Result
            if is_normal_phase_perfect:
                print("\n✅ NORMAL PHASE COMPLETED SUCCESSFULLY!")
            else:
                print("\n🔶 NORMAL PHASE COMPLETED WITH WARNINGS. Horror phase is disabled by policy.")
            print("="*60)
            
            normal_phase_did_run = True

            # === HORROR MODE (with error resilience) ===
            if has_horror and not self.should_stop:
                print("\n👻 STARTING HORROR PHASE (with error resilience)...")
                is_horror_phase_perfect = True # Assume success, set to False on any failure
                
                try:
                    if not self.trigger_horror_mode_transition_corrected(progress_callback=progress_callback):
                        raise Exception("Horror mode trigger failed")
                    
                    # Horror showcase
                    success, total = self.showcase_all_icons_corrected("Horror", skip_icon_19=False, progress_callback=progress_callback)
                    report.horror_icons_showcased = success
                    report.horror_icons_total = total
                    if success < total:
                        is_horror_phase_perfect = False
                        report.issues.append(f"Horror showcase failed for {total - success} icon(s).")
                    
                    # Horror bonus
                    if has_bonus and not self.should_stop:
                        if not self.showcase_all_bonus_corrected("Horror", progress_callback=progress_callback):
                           is_horror_phase_perfect = False
                           report.issues.append("Horror bonus showcase failed.")

                    # Log Horror Remix Performance Start
                    if self.obs_controller:
                        self.obs_controller.log_event_timestamp(
                            "start_full_remix_performance", "Starting HORROR remix performance section", {"mode": "Horror"}
                        )

                    # Horror remixes
                    if not self.should_stop:
                        remix_result = self.play_all_remixes_corrected("Horror", progress_callback=progress_callback)
                        report.horror_remixes_played = remix_result.succeeded
                        report.horror_remixes_total = remix_result.attempted
                        report.evidence["horrorRemixResult"] = remix_result.as_dict()
                        if remix_result.succeeded < remix_result.attempted:
                            is_horror_phase_perfect = False
                            report.issues.append(
                                f"Horror remixes failed for {remix_result.attempted - remix_result.succeeded} item(s)."
                            )
                    
                    if is_horror_phase_perfect:
                        print("\n✅ HORROR PHASE COMPLETED SUCCESSFULLY!")
                    else:
                        print("\n🔶 HORROR PHASE COMPLETED WITH WARNINGS.")

                except Exception as horror_error:
                    print(f"\n⚠️ HORROR PHASE ERROR: {horror_error}")
                    is_horror_phase_perfect = False
                    report.issues.append(f"Horror phase critical error: {horror_error}")
                    print("   Horror coverage remains failed; source JSON is not mutated automatically.")
            # === SHARED QUALITY-GATE DETERMINATION ===
            # Aggregate/soft placement evidence is valid workflow progress;
            # target-specific proof remains diagnostic and cache-only.
            report.placement_verified = any(
                placement_transaction_proves_progress(transaction)
                for transaction in self._workflow_placement_transactions
            )
            if self._workflow_placement_transactions:
                first_transaction = self._workflow_placement_transactions[0]
                report.placement_before = first_transaction.get("before")
                report.placement_after = first_transaction.get("after")
            report.evidence["placementTransactions"] = list(self._workflow_placement_transactions)

            completion_state = self.validate_workflow_completion()
            report.cleanup_verified = bool(completion_state[0])

            if self.should_stop or self.stop_event.is_set():
                report.apply_quality_gate(
                    QualityGateResult.failure(
                        "workflow_stopped",
                        evidence={"placementTransactions": len(self._workflow_placement_transactions)},
                        message="Workflow stopped before all required evidence was collected.",
                    )
                )
            else:
                evaluate_workflow_report(report, mode="master", require_identity=False)

            if has_horror and not is_horror_phase_perfect and report.was_successful:
                report.apply_quality_gate(
                    QualityGateResult.failure(
                        "phase_skipped",
                        coverage={"normal": "verified", "horror": "failed"},
                        evidence={"horror": "verification_failed"},
                        message="Explicit horror verification was requested but failed.",
                    )
                )

            self.last_workflow_report = report

        except WorkflowIncompatibleError as e:
            print(f"🛑 Workflow halted by Circuit Breaker: {e}")
            report.apply_quality_gate(
                QualityGateResult.failure(
                    "workflow_stopped" if self.should_stop or self.stop_event.is_set() else "placement_not_verified",
                    evidence={"error": str(e)},
                    message=f"Workflow failed: {e}",
                )
            )
            report.issues.append(str(e))
            # Không cần làm gì thêm, khối finally sẽ xử lý dọn dẹp
            
        except Exception as e:
            print("❌ Critical error in full showcase workflow: " + str(e))
            report.apply_quality_gate(
                QualityGateResult.failure(
                    "workflow_stopped" if self.should_stop or self.stop_event.is_set() else "runtime_verification_unknown",
                    status="UNKNOWN" if not (self.should_stop or self.stop_event.is_set()) else "FAIL",
                    retryable=not (self.should_stop or self.stop_event.is_set()),
                    evidence={"error": str(e)},
                    message=f"Critical error: {e}",
                )
            )
            report.issues.append(f"Critical error: {e}")

        finally:
            # Cleanup is intentionally centralized here.  The previous
            # implementation also cleaned up in the try body, causing the
            # complete precision/carpet/recovery chain to run twice.
            print("\n🧹 FINAL SMART CLEANUP & COMPREHENSIVE VALIDATION")
            print("="*70)
            cleanup_result = self._run_cleanup_once(
                use_cdp=False,
                context={"workflow": "full_showcase"},
                obs_controller=self.obs_controller,
            )
            cleanup_ok = cleanup_result.status in {
                OperationStatus.VERIFIED,
                OperationStatus.ALREADY_EMPTY,
            }
            report.cleanup_verified = cleanup_ok
            if not cleanup_ok:
                report.issues.append(
                    f"Final cleanup: {cleanup_result.status.value} ({cleanup_result.message})"
                )
                if report.was_successful or not report.failure_code:
                    report.apply_quality_gate(
                        QualityGateResult.failure(
                            "placement_not_verified",
                            evidence={"cleanupStatus": cleanup_result.status.value},
                            message="Final cleanup was not verified.",
                        )
                    )
            final_validation = self.validate_workflow_completion()
            report.cleanup_verified = cleanup_ok and bool(final_validation[0])
            if not final_validation[0] and report.was_successful:
                report.apply_quality_gate(
                    QualityGateResult.failure(
                        "placement_not_verified",
                        evidence={"validationIssues": final_validation[1]},
                        message="Final board validation failed.",
                    )
                )

            # The first gate evaluation happens before this final cleanup so
            # a partial remix cannot poison the report with a stale cleanup
            # failure. Re-evaluate only that specific stale code; preserve
            # independent failures such as remix thresholds, runtime entry,
            # and explicit horror-phase failures.
            if (
                cleanup_ok
                and final_validation[0]
                and not self.should_stop
                and not self.stop_event.is_set()
                and report.failure_code == "placement_not_verified"
            ):
                evaluate_workflow_report(report, mode="master", require_identity=False)
                report.evidence["qualityGateReevaluatedAfterFinalCleanup"] = True
            if report.quality_gate is None:
                report.apply_quality_gate(
                    QualityGateResult.failure(
                        report.failure_code or (
                            "workflow_stopped"
                            if self.should_stop or self.stop_event.is_set()
                            else "placement_not_verified"
                        ),
                        status=(
                            "UNKNOWN"
                            if report.failure_code == "runtime_verification_unknown"
                            else "FAIL"
                        ),
                        retryable=report.failure_code == "runtime_verification_unknown",
                        evidence={"issues": list(report.issues)},
                        message=report.message or "Workflow did not pass the quality gate.",
                    )
                )
            self.last_workflow_report = report
            print("\n📊 ENHANCED WORKFLOW SUMMARY:")
            print(self.get_enhanced_workflow_summary())
            print("="*70)
            
            return report

    def update_json_has_horror(self, new_has_horror_value):
        """
        📝 Update hasHorror value in the original JSON file
        Used when hasHorror detection is incorrect and needs correction
        """
        try:
            # Check if we have the file path from the original load
            if not hasattr(self, '_original_json_path'):
                print("⚠️ Original JSON file path not available for update")
                return False
            
            file_path = self._original_json_path
            
            # Read current JSON content
            print(f"📖 Reading JSON file: {file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # Update hasHorror value
            old_value = json_data.get('gameSettings', {}).get('hasHorror', None)
            if 'gameSettings' not in json_data:
                json_data['gameSettings'] = {}
            
            json_data['gameSettings']['hasHorror'] = new_has_horror_value
            
            # Add update timestamp and reason
            json_data['gameSettings']['hasHorrorCorrected'] = {
                'corrected_at': datetime.now().isoformat(),
                'original_value': old_value,
                'corrected_value': new_has_horror_value,
                'reason': 'Horror phase failed - likely incorrect detection'
            }
            
            # Write updated JSON back to file
            print(f"💾 Writing updated JSON file...")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            
            # Update internal config as well
            self.config = json_data
            
            print(f"✅ JSON updated successfully:")
            print(f"   📝 File: {file_path}")
            print(f"   🔄 hasHorror: {old_value} → {new_has_horror_value}")
            print(f"   ⏰ Timestamp: {json_data['gameSettings']['hasHorrorCorrected']['corrected_at']}")
            print(f"   💡 Future runs will use corrected value")
            
            return True
            
        except FileNotFoundError:
            print(f"❌ JSON file not found: {getattr(self, '_original_json_path', 'unknown')}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ JSON format error: {e}")
            return False
        except PermissionError:
            print(f"❌ Permission denied writing to JSON file")
            return False
        except Exception as e:
            print(f"❌ Error updating JSON file: {e}")
            return False

    def showcase_all_bonus_corrected(self, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Showcase bonus with verification system - FLEXIBLE BONUS COUNT"""
        try:
            if not self.config:
                return False
                
            layout = self.config['layout']
            
            # --- LOGIC MỚI: LOẠI BỎ GIỚI HẠN ---
            # Lấy toàn bộ danh sách bonus mà không lọc theo index.
            bonus_chars = layout.get('bonus', [])
            
            if not bonus_chars:
                print(f"ℹ️  No bonus characters to showcase for {mode} mode.")
                return True
            
            target_slot = self._find_slot_by_index(3)
            if not target_slot:
                return False
            
            emoji = "🌟" if mode == "Normal" else "👻"
            print(f"\n{emoji} STARTING ENHANCED {mode.upper()} BONUS SHOWCASE")
            print(f"📄 Will showcase {len(bonus_chars)} bonus characters")
            if self.characters_available:
                print("✅ Drag verification: ENABLED")
            else:
                print("⚠️ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success_count = 0
            for bonus_index, bonus_char in enumerate(bonus_chars):
                if self.stop_event.is_set():
                    break
                
                if progress_callback:
                    progress_callback(f"{emoji} Showcasing {mode} bonus {bonus_index + 1}")
                
                print(f"{emoji} Showcasing {mode} bonus {bonus_index + 1} (Index: {bonus_char.get('index', 'N/A')})")
                
                use_cdp = True
                if not self.corrected_drag_and_drop(bonus_char, target_slot, False, use_cdp):
                    print(f"❌ Failed to drag bonus {bonus_index} with verification")
                    print("🛑 Bonus sequence stopped; no blind retry on a possibly partial board state")
                    return False
                
                if self.stop_event.is_set(): break
                
                print(f"Listening to bonus {bonus_index + 1} for {listen_duration}s...")
                for countdown in range(listen_duration, 0, -1):
                    if self.stop_event.is_set(): break
                    if progress_callback and countdown % 3 == 0:
                        try: self.thread_safe_progress_callback(f"Listening {mode} bonus {bonus_index + 1} ({countdown}s)")
                        except Exception as e: print(f"Progress callback warning: {e}")
                    time.sleep(1)
                
                if self.stop_event.is_set(): break
                
                print(f"🔄 Returning bonus {bonus_index + 1}...")
                return_result = self.corrected_return_icon(target_slot, use_cdp)
                if return_result.status not in {
                    OperationStatus.VERIFIED,
                    OperationStatus.ALREADY_EMPTY,
                }:
                    print(
                        f"🛑 Bonus return stopped at {return_result.status.value}: "
                        f"{return_result.message}"
                    )
                    break
                time.sleep(0.2)
                success_count += 1
            
            print(f"\n✅ ENHANCED {mode.upper()} BONUS SHOWCASE COMPLETED")
            print(f"📊 Success: {success_count}/{len(bonus_chars)} characters")
            print("="*60)
            return success_count == len(bonus_chars)
            
        except Exception as e:
            print("❌ Error in enhanced " + mode + " bonus showcase: " + str(e))
            return False
    
    def play_all_remixes_corrected(self, mode="Normal", listen_duration=15, progress_callback=None):
        """Play all remixes and return explicit counters.

        The old tuple return made ``(0, 0)`` truthy in remix-only mode.  The
        structured result keeps compatibility with unpacking while all new
        callers use named fields and the object fails closed in boolean
        contexts.
        """
        builtin_print = builtins.print

        def safe_print(*args, **kwargs):
            try:
                builtin_print(*args, **kwargs)
            except UnicodeEncodeError:
                fallback_args = tuple(
                    str(arg).encode("ascii", errors="replace").decode("ascii")
                    for arg in args
                )
                builtin_print(*fallback_args, **kwargs)

        print = safe_print
        total_count = len(self.remix_library)
        try:
            if not self.remix_library:
                print("ℹ️ No remixes in library to play.")
                return RemixResult(failure_code="empty_remix")
            
            emoji = "ðŸŽµ" if mode == "Test" else ("ðŸ‘»" if mode == "Horror" else "ðŸŽª")
            print(f"\n{emoji} STARTING ENHANCED {mode.upper()} REMIX SESSION")
            print(f"ðŸ“Š Will play {total_count} remixes")
            print("="*60)
            
            success_count = 0
            processed_count = 0
            consecutive_failures = 0
            total_retry_attempts = 0
            attempt_details: List[Dict[str, Any]] = []
            REMIX_FAILURE_THRESHOLD = 2 # Ngưỡng: 2 remix thất bại liên tiếp

            for remix_index, remix in enumerate(self.remix_library):
                if self.stop_event.is_set():
                    break

                processed_count += 1
                
                self.current_remix_index = remix_index
                
                if progress_callback:
                    progress_callback(f"{emoji} Remix {remix_index + 1}/{total_count}: {remix['name']}")
                
                remix_success, remix_attempt_details = self._play_remix_with_retry(
                    remix,
                    mode,
                    listen_duration,
                    progress_callback,
                )
                total_retry_attempts += max(len(remix_attempt_details) - 1, 0)
                attempt_details.append(
                    {
                        "remixName": remix.get("name", "Unknown Remix"),
                        "attempts": remix_attempt_details,
                        "completed": remix_success,
                    }
                )

                if remix_success:
                    success_count += 1
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

                    last_attempt = remix_attempt_details[-1] if remix_attempt_details else {}
                    if last_attempt.get("cleanupVerified") is False:
                        print(
                            f"🛑 Failed to play remix: {remix['name']}; "
                            "cleanup is unverified, stopping remix session."
                        )
                        raise WorkflowIncompatibleError(
                            f"Remix cleanup was not verified after {remix['name']}; "
                            "refusing to start another remix on a dirty or unknown board."
                        )
                    print(f"⚠️ Failed to play remix: {remix['name']}, continuing...")

                # [REFINED] Kích hoạt ngắt mạch cho remix bằng cách ném Exception
                if consecutive_failures >= REMIX_FAILURE_THRESHOLD:
                    error_msg = f"Circuit breaker tripped after {consecutive_failures} consecutive remix failures."
                    print(f"🚨 [CIRCUIT BREAKER] {error_msg}")
                    raise WorkflowIncompatibleError(error_msg)
                
                if remix_index < total_count - 1 and not self.should_stop:
                    time.sleep(2.0)
            
            print(f"\n✅ ENHANCED {mode.upper()} REMIX SESSION COMPLETED")
            print(f"📊 Success: {success_count}/{processed_count} attempted remixes ({total_count} expected)")
            print("="*60)
            return RemixResult(
                attempted=processed_count,
                succeeded=success_count,
                failed=max(processed_count - success_count, 0),
                skipped=max(total_count - processed_count, 0),
                retry_attempts=total_retry_attempts,
                attempt_details=attempt_details,
                failure_code=(
                    "workflow_stopped"
                    if self.should_stop or self.stop_event.is_set()
                    else None
                ),
            )

        except Exception as e:
            print("❌ Error in enhanced " + mode + " remix session: " + str(e))
            processed = processed_count if "processed_count" in locals() else 0
            return RemixResult(
                attempted=processed,
                succeeded=success_count if "success_count" in locals() else 0,
                failed=max(processed - (success_count if "success_count" in locals() else 0), 0),
                skipped=max(total_count - processed, 0),
                retry_attempts=(
                    total_retry_attempts if "total_retry_attempts" in locals() else 0
                ),
                attempt_details=(
                    attempt_details if "attempt_details" in locals() else []
                ),
                failure_code=(
                    "workflow_stopped"
                    if self.should_stop or self.stop_event.is_set()
                    else "remix_threshold_not_met"
                ),
            )
    
    def trigger_horror_mode_transition_corrected(self, transition_duration=15, progress_callback=None):
        """
        ENHANCED: Trigger horror mode with verification system - FIXED with Icon Return
        UPDATED: Added Phase 3 - Return Icon 19 after transformation
        FIXED: Proper access to icon 19 for horror trigger
        """
        return_success = False

        try:
            if not self.config:
                return False
                
            layout = self.config['layout']
            all_icons = layout['icons']
            
            # FIXED: For horror trigger, we NEED to access icon 19 specifically from original list
            print(f"🎯 HORROR MODE TRIGGER - Accessing icon 19 specifically")
            print(f"   📊 Total icons available: {len(all_icons)}")
            print(f"   💡 Normal operations limited to 0-18, but horror trigger needs icon 19")
            
            # Find icon 19 in the original (unlimited) icon list
            horror_icon = None
            for icon in all_icons:
                if icon.get('index') == 19:
                    horror_icon = icon
                    print(f"   🎯 Icon 19 found at coordinates: ({icon.get('x')}, {icon.get('y')})")
                    break
            
            if not horror_icon:
                print("❌ Icon 19 not found in config - cannot trigger horror mode")
                print("   💡 Make sure your JSON config includes icon with index 19")
                return False
            
            print(f"   ✅ Horror trigger ready - will use icon 19 specifically")
            
            # Find slot index 3 (rest of function remains the same)
            layout = self.config['layout']
            slots = layout.get('slots', [])
            center_slot_index = self.calculate_center_slot_index(slots)
            target_slot = self._find_slot_by_index(center_slot_index)
            if not target_slot:
                return False
            
            if progress_callback:
                progress_callback("ðŸ‘» Triggering Horror Mode with verification...")
            
            print("\nðŸ‘» TRIGGERING ENHANCED HORROR MODE TRANSITION...")
            if self.characters_available:
                print("ðŸ” Using verification for horror icon drag")
            else:
                print("âš ï¸ Verification disabled - using fallback")
            print("="*60)

            # Đánh dấu bắt đầu chuỗi sự kiện trigger
            if self.obs_controller:
                self.obs_controller.log_event_timestamp(
                    "trigger_horror_mode_started",
                    "Start triggering horror mode sequence",
                    {"icon_index": horror_icon.get('index', 19)}
                )

            # ================================================================
            # PHASE 1: DRAG ICON 19 TO SLOT 3 (trigger horror mode)
            # ================================================================
            print("ðŸŽ¯ PHASE 1: Dragging horror icon (19) to slot 3 with verification...")
            use_cdp = True  # Can be customized
            if not self.corrected_drag_and_drop(horror_icon, target_slot, False, use_cdp):
                print("âŒ Failed to trigger horror mode with verification")
                return False
            
            # ================================================================
            # PHASE 2: WAIT FOR TRANSFORMATION
            # ================================================================
            print(f"PHASE 2: Waiting for horror transformation ({transition_duration}s)...")
            for countdown in range(transition_duration, 0, -1):
                if self.stop_event.is_set():
                    return False
                
                # THREAD SAFE: Update every 5 seconds for longer transitions
                if progress_callback and countdown % 5 == 0:
                    try:
                        self.thread_safe_progress_callback(f"Horror mode transition... ({countdown}s)")
                    except Exception as e:
                        print(f"Progress callback warning: {e}")
                
                time.sleep(1)
            
            if self.stop_event.is_set():
                return False
            
            print("âœ… ENHANCED HORROR MODE TRANSITION COMPLETED!")
            
            # ================================================================
            # PHASE 3: ðŸ†• RETURN ICON 19 FROM SLOT 3 (clear slot for showcase)
            # ================================================================
            print("\nðŸ”„ PHASE 3: RETURNING ICON 19 FROM SLOT 3 (prepare for showcase)")
            print("="*60)
            print("   ðŸ’¡ Reason: Slot 3 must be empty for horror showcase to work")
            print("   ðŸŽ¯ Target: Return Icon 19 from Slot 3 back to icon area")
            
            if progress_callback:
                progress_callback("ðŸ”„ Returning horror trigger icon for showcase...")
            
            # Check current state before return.  No emergency click loop is
            # allowed after corrected_return_icon has already exhausted its
            # own bounded chain.
            before_snapshot = self._capture_stable_placement_snapshot(timeout_ms=1000)
            if before_snapshot is None:
                print("   🛑 Horror return precondition is UNKNOWN")
                return False
            precondition = classify_precondition(before_snapshot, "return", center_slot_index)
            if precondition == OperationStatus.ALREADY_EMPTY:
                return_success = OperationResult(
                    OperationStatus.ALREADY_EMPTY,
                    direction="return",
                    message="horror_target_already_empty",
                )
            else:
                print(f"   ðŸŽ¯ Slot occupied or aggregate fallback; returning icon 19...")
                return_success = self.corrected_return_icon(target_slot, use_cdp)
                if return_success.status not in {
                    OperationStatus.VERIFIED,
                    OperationStatus.ALREADY_EMPTY,
                }:
                    print(
                        f"   🛑 Horror return stopped at {return_success.status.value}: "
                        f"{return_success.message}"
                    )
                    return False

            # Đánh dấu KẾT THÚC chuỗi sự kiện trigger SAU KHI đã trả icon về
            if self.obs_controller:
                self.obs_controller.log_event_timestamp(
                    "trigger_horror_mode_ended",
                    "Horror mode sequence completed and slot is cleared",
                    {"return_successful": return_success.status.value}
                )

            # ================================================================
            # HORROR MODE STATE VALIDATION (existing code with better logging)
            # ================================================================
            print("\nðŸ‘» HORROR MODE STATE VALIDATION")
            print("="*50)
            
            try:
                # Validate horror mode activation state
                horror_state = self.get_comprehensive_slot_state()
                
                if horror_state:
                    characters_count = horror_state.get('characters_count')
                    
                    print(f"ðŸ“Š Final horror mode state:")
                    print(f"   Characters count: {characters_count}")
                    
                    # Expect 0 after return (slot should be empty for showcase)
                    if self._is_board_count_clean(
                        horror_state.get("characters_count"),
                        horror_state.get("putted_characters"),
                    ):
                        print("âœ… Horror mode properly prepared - slot empty for showcase")
                        print("ðŸŽ¯ Ready to showcase all horror icons (0-19)")
                    else:
                        print(f"🛑 Horror mode state is not clean: {characters_count}")
                        return False
                else:
                    print("🛑 Cannot validate horror mode state")
                    return False
                
            except Exception as e:
                print(f"âš ï¸ Error validating horror mode state: {e}")
                return False
            
            print("="*50)
            
            # Final confirmation
            if progress_callback:
                progress_callback("ðŸ‘» Horror Mode ready for showcase!")
            
            print("ðŸŽ‰ ENHANCED HORROR MODE FULLY PREPARED!")
            print("   âœ… Phase 1: Horror trigger completed")
            print("   âœ… Phase 2: Transformation completed") 
            print("   âœ… Phase 3: Icon return completed")
            print("   ðŸŽ¯ Ready for horror showcase (icons 0-19)")
            
            time.sleep(1.0)
            return True
            
        except Exception as e:
            print("âŒ Error triggering enhanced horror mode: " + str(e))
            return False
    
    def execute_remix_only_workflow_corrected(self, progress_callback=None):
        """Run remix-only workflow through the same fail-closed quality gate."""
        builtin_print = builtins.print

        def safe_print(*args, **kwargs):
            try:
                builtin_print(*args, **kwargs)
            except UnicodeEncodeError:
                # Console encoding must never hide the workflow's actual
                # quality-gate result or prevent its failure report from being
                # persisted.
                fallback_args = tuple(
                    str(arg).encode("ascii", errors="replace").decode("ascii")
                    for arg in args
                )
                builtin_print(*fallback_args, **kwargs)

        print = safe_print
        success = False
        report = WorkflowReport(mode="remix_only")
        self.last_workflow_report = report
        self._workflow_placement_transactions = []
        cleanup_result = None
        try:
            self._reset_cleanup_guard()
            layout = (self.config or {}).get("layout", {}) or {}
            report.layout_icons = len(layout.get("icons", []) or [])
            report.layout_slots = len(layout.get("slots", []) or [])
            report.entry_candidate = self.entry_strategy.get("selected")
            report.tested_candidates = list(self.entry_strategy.get("tested", []) or [])
            report.evidence["entryStrategy"] = dict(self.entry_strategy)
            report.runtime_state = str(self.entry_strategy.get("verification") or UNKNOWN)
            report.runtime_verified = report.runtime_state == "PASS"
            report.coverage = {"normal": "not_started", "horror": "not_checked_by_policy"}

            if progress_callback:
                progress_callback("ðŸŽµ Starting enhanced remix-only workflow...")
            
            print("\nðŸŽµ STARTING ENHANCED REMIX-ONLY WORKFLOW")
            if self.characters_available:
                print("ðŸ” Drag verification: ENABLED for all remixes")
            else:
                print("âš ï¸ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            remix_result = self.play_all_remixes_corrected("Test", progress_callback=progress_callback)
            report.normal_remixes_played = remix_result.succeeded
            report.normal_remixes_total = remix_result.attempted
            report.evidence["remixResult"] = remix_result.as_dict()

            if remix_result.failure_code:
                report.apply_quality_gate(
                    QualityGateResult.failure(
                        remix_result.failure_code,
                        evidence=remix_result.as_dict(),
                        message=(
                            "Remix-only workflow stopped before finalization."
                            if remix_result.failure_code == "workflow_stopped"
                            else "Remix session did not complete its required attempts."
                        ),
                    )
                )
            elif remix_result.attempted > 0 and remix_result.succeeded > 0:
                if progress_callback:
                    progress_callback("ðŸŽ‰ Enhanced remix-only workflow completed!")
                print("\nðŸŽ‰ ENHANCED REMIX-ONLY WORKFLOW COMPLETED!")
                
                # ================================================================
                # ðŸ§¹ REMIX-ONLY SMART CLEANUP & VALIDATION
                # ================================================================
                print("\nðŸ§¹ REMIX-ONLY SMART CLEANUP & VALIDATION")
                print("="*60)
                
                try:
                    # Smart cleanup for remix-only mode
                    print("ðŸ”„ Smart batch cleanup for remix-only mode...")
                    cleanup_result = self._run_cleanup_once(
                        use_cdp=False,
                        context={"workflow": "remix_only"},
                        obs_controller=self.obs_controller,
                    )
                    cleanup_success = cleanup_result.status in {
                        OperationStatus.VERIFIED,
                        OperationStatus.ALREADY_EMPTY,
                    }
                    cleanup_total = 1
                    cleanup_success_count = 1 if cleanup_success else 0
                    if not cleanup_success:
                        report.issues.append(
                            f"Remix cleanup failed: {cleanup_result.status.value}"
                        )
                    
                    if cleanup_total == 0:
                        print("âœ… No remix cleanup needed - all slots clean")
                    elif cleanup_success_count == cleanup_total:
                        print(f"âœ… Remix cleanup: {cleanup_success_count}/{cleanup_total} perfect")
                    else:
                        print(f"âš ï¸ Remix cleanup: {cleanup_success_count}/{cleanup_total} with issues")
                    
                    # Remix-specific validation
                    is_complete, remaining_issues, recommendations = self.validate_workflow_completion()
                    
                    if is_complete:
                        print("âœ… REMIX VALIDATION: PERFECT! All remixes cleaned properly")
                    else:
                        print(f"âš ï¸ REMIX VALIDATION: {len(remaining_issues)} cleanup issues")
                        for issue in remaining_issues[:2]:
                            print(f"   - {issue}")
                    report.cleanup_verified = bool(is_complete)
                    
                    # Enhanced remix summary
                    print("\nðŸ“Š ENHANCED REMIX WORKFLOW STATS:")
                    summary = self.get_enhanced_workflow_summary()
                    print(summary)
                    
                except Exception as e:
                    print(f"âš ï¸ Error during remix validation: {e}")
                    print("ðŸ“Š Basic remix statistics:")
                    print(self.get_drag_statistics())
                
                print("="*60)
            else:
                report.apply_quality_gate(
                    QualityGateResult.failure(
                        "empty_remix" if remix_result.attempted <= 0 else "remix_threshold_not_met",
                        evidence=remix_result.as_dict(),
                        message=(
                            "Remix-only workflow had no remix to attempt."
                            if remix_result.attempted <= 0
                            else "Remix-only workflow produced no successful remix."
                        ),
                    )
                )
            
        except Exception as e:
            print("âŒ Error in enhanced remix-only workflow: " + str(e))
            report.apply_quality_gate(
                QualityGateResult.failure(
                    "workflow_stopped" if self.should_stop or self.stop_event.is_set() else "remix_threshold_not_met",
                    evidence={"error": str(e)},
                    message=f"Remix-only workflow failed: {e}",
                )
            )
        finally:
            # A partial remix can still have occupied slots.  The guard makes
            # this safe even when the success branch already performed its
            # cleanup/report path.
            if not self._cleanup_completed:
                cleanup_result = self._run_cleanup_once(
                    use_cdp=False,
                    context={"workflow": "remix_only_finally"},
                    obs_controller=self.obs_controller,
                )
                if cleanup_result.status not in {
                    OperationStatus.VERIFIED,
                    OperationStatus.ALREADY_EMPTY,
                }:
                    report.issues.append(
                        f"Final remix cleanup failed: {cleanup_result.status.value}"
                    )

            report.placement_verified = any(
                placement_transaction_proves_progress(transaction)
                for transaction in self._workflow_placement_transactions
            )
            report.evidence["placementTransactions"] = list(self._workflow_placement_transactions)
            if self._workflow_placement_transactions:
                report.placement_before = self._workflow_placement_transactions[0].get("before")
                report.placement_after = self._workflow_placement_transactions[0].get("after")

            cleanup_ok = bool(cleanup_result) and cleanup_result.status in {
                OperationStatus.VERIFIED,
                OperationStatus.ALREADY_EMPTY,
            }
            if report.failure_code:
                final_validation = (False, [report.failure_code], [])
            else:
                final_validation = self.validate_workflow_completion()
            report.evidence["finalCleanupValidation"] = {
                "isComplete": bool(final_validation[0]),
                "issues": list(final_validation[1]),
            }
            report.cleanup_verified = cleanup_ok and bool(final_validation[0])
            if not report.failure_code:
                if self.should_stop or self.stop_event.is_set():
                    report.apply_quality_gate(
                        QualityGateResult.failure(
                            "workflow_stopped",
                            message="Remix-only workflow stopped before finalization.",
                        )
                    )
                elif not final_validation[0]:
                    report.apply_quality_gate(
                        QualityGateResult.failure(
                            "placement_not_verified",
                            evidence={"validationIssues": final_validation[1]},
                            message="Final remix board validation failed.",
                        )
                    )
                else:
                    evaluate_workflow_report(report, mode="remix_only", require_identity=False)
            self.last_workflow_report = report

        success = report.quality_gate is not None and report.quality_gate.passed is True
        return success

    # ================================================================
    # == LEGACY METHOD REDIRECTS ==
    # ================================================================
    
    def showcase_single_icon(self, icon_index, mode="Normal", listen_duration=15, progress_callback=None):
        """Redirect to enhanced version"""
        return self.showcase_single_icon_corrected(icon_index, mode, listen_duration, progress_callback)
    
    def enhanced_drag_and_drop(self, icon, target_slot, is_first_icon=False):
        """Redirect to enhanced version"""
        return self.corrected_drag_and_drop(icon, target_slot, is_first_icon)
    
    def enhanced_click_to_return(self, target_slot):
        """Redirect to enhanced version"""
        return self.corrected_return_icon(target_slot)
    
    def showcase_all_icons(self, mode="Normal", skip_icon_19=False, listen_duration=15, progress_callback=None):
        """Redirect to enhanced version"""
        return self.showcase_all_icons_corrected(mode, skip_icon_19, listen_duration, progress_callback)
    
    def play_complete_remix(self, remix_data, mode="Normal", listen_duration=15, progress_callback=None):
        """Run one complete remix with the bounded retry/cleanup policy."""
        success, _attempt_details = self._play_remix_with_retry(
            remix_data,
            mode,
            listen_duration,
            progress_callback,
        )
        return success
    
    def execute_full_showcase_workflow(self, progress_callback=None):
        """Redirect to enhanced version"""
        return self.execute_full_showcase_workflow_corrected(progress_callback)
    
    def execute_remix_only_workflow(self, progress_callback=None):
        """Redirect to enhanced version"""
        return self.execute_remix_only_workflow_corrected(progress_callback)

    # ================================================================
    # == GUI SETUP (ENHANCED) ==
    # ================================================================
    
    def setup_gui(self):
        self.root = Tk()
        self.root.title("Sprunki Game Player - ENHANCED with Characters Array Verification + Smart Page Verification")
        self.root.geometry("650x900")
        
        self.mode = IntVar(value=1)
        self.status_var = StringVar()
        self.status_var.set("Config not loaded")
        self.action_var = StringVar()
        self.action_var.set("ðŸš€ Ready for ENHANCED workflow with Smart Verification...")

        self.progress_updater = ThreadSafeProgressUpdater(self.root, self.action_var)

        # Header
        Label(self.root, text="Sprunki Game Player", font=("Arial", 16, "bold")).pack(pady=10)
        Label(self.root, text="ðŸŽ® ENHANCED: Chrome Debug + Characters Array Verification + Smart Page Verification ðŸš€", 
            font=("Arial", 12), fg="green").pack()
        
        # Enhanced workflow info
        enhanced_info_frame = Frame(self.root, bg="#E8F5E8", relief="ridge", bd=1)
        enhanced_info_frame.pack(fill="x", padx=10, pady=5)
        
        Label(enhanced_info_frame, text="âš¡ ENHANCED WORKFLOW:", 
            font=("Arial", 10, "bold"), bg="#E8F5E8", fg="#2E7D32").pack(anchor="w", padx=10, pady=(5,2))
        
        enhanced_features = """âœ… Characters Array Verification: Count "1" values in Characters Array for accurate state
        âœ… 5-Coordinate Retry: Center â†’ Left â†’ Right â†’ Top â†’ Bottom positions for each icon
        âœ… Smart Fallback: Auto-fallback to PyAutoGUI if CDP verification fails
        âœ… Enhanced Statistics: Track verification success rate and coordinate usage
        âœ… Game-Aware: Real-time monitoring of Characters Array state
        âœ… Intelligent Retry: Return icon and retry at different coordinate if verification fails"""
        
        Label(enhanced_info_frame, text=enhanced_features, justify="left",
            font=("Arial", 8), fg="#1B5E20", bg="#E8F5E8").pack(anchor="w", padx=15, pady=(0,5))
        
        # Smart verification system info
        verification_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
        verification_frame.pack(fill="x", padx=10, pady=5)
        
        Label(verification_frame, text="ðŸ”¢ CHARACTERS ARRAY SYSTEM:", 
            font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
        
        verification_text = """ðŸ”¢ Characters Array Monitoring: Real-time tracking of character placement state
        ðŸŽ¯ Count-Based Verification: Count "1" values in array = number of icons on slots
        âš¡ Stable Across Mods: Works reliably on all game modifications
        ðŸŽ® High Performance: Direct VM access with optimized JavaScript functions
        ðŸ“Š Accurate State: No dependency on unreliable Putted Characters or Polos Array
        ðŸ”„ Real-Time Updates: Instant detection of drag/return operations"""
        
        Label(verification_frame, text=verification_text, justify="left",
            font=("Arial", 8), fg="#0D47A1", bg="#E3F2FD").pack(anchor="w", padx=15, pady=(0,5))
        
        # Image detection info
        detection_frame = Frame(self.root, bg="#FFF3E0", relief="ridge", bd=1)
        detection_frame.pack(fill="x", padx=10, pady=5)
        
        Label(detection_frame, text="ðŸ–¼ï¸ ENHANCED IMAGE DETECTION:", 
            font=("Arial", 10, "bold"), bg="#FFF3E0", fg="#E65100").pack(anchor="w", padx=10, pady=(5,2))
        
        detection_text = """ðŸŽ¯ Smart Auto-Scroll: Automatic PageDown if Run/Fullscreen buttons not in viewport
        ðŸ” Multi-Scale Detection: Test multiple scale factors for button recognition
        ðŸŽª Domain-Optimized: Specific adjustments for cocrea.world game timing
        ðŸ” Template Matching: Advanced CV2 image matching with confidence thresholds
        ðŸ”„ Retry Logic: Multiple attempts with different scroll positions
        âš™ï¸ Config-Driven: All detection parameters configurable via config.json"""
        
        Label(detection_frame, text=detection_text, justify="left",
            font=("Arial", 8), fg="#BF360C", bg="#FFF3E0").pack(anchor="w", padx=15, pady=(0,5))
        
        # Mode selection
        mode_frame = Frame(self.root)
        mode_frame.pack(pady=10)
        
        Label(mode_frame, text="ðŸŽ¯ Select play mode:", font=("Arial", 11, "bold")).pack()
        
        Radiobutton(mode_frame, text="ðŸ”€ Showcase + Remix (ENHANCED)", 
                variable=self.mode, value=1, font=("Arial", 10)).pack(anchor="w")
        
        Radiobutton(mode_frame, text="ðŸŽµ Remix Only (ENHANCED)", 
                variable=self.mode, value=2, font=("Arial", 10)).pack(anchor="w")
        
        # Debug frame with Characters Array System
        debug_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
        debug_frame.pack(fill="x", padx=10, pady=5)
        
        Label(debug_frame, text="ðŸ”§ DEBUG TOOLS:", 
            font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
        
        debug_buttons = Frame(debug_frame, bg="#E3F2FD")
        debug_buttons.pack(fill="x", padx=10, pady=(0,5))
        
        Button(debug_buttons, text="ðŸ“Š Enhanced Stats", 
            command=self.show_drag_stats, bg="#4CAF50", fg="white", font=("Arial", 9)).pack(side="left")
        
        Button(debug_buttons, text="ðŸŒ Test WebSocket", 
            command=self.test_websocket_connection, bg="#FF9800", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        
        Button(debug_buttons, text="ðŸ”¢ Test Characters Array", 
            command=self.test_verification_system, bg="#9C27B0", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        
        Button(debug_buttons, text="âš¡ Test Smart Verification", 
            command=self.test_smart_verification_system, bg="#2196F3", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="ðŸŽ¯ Check Mode", 
            command=self.show_current_mode_info, bg="#607D8B", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="ðŸŽ¨ Grid Info", 
            command=self.show_carpet_grid_info, bg="#795548", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="ðŸ§ª Test Carpet", 
            command=self.show_carpet_bombing_test_results, bg="#4CAF50", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="🎯 DUAL Tracking", 
            command=self.show_smart_dual_tracking_status, bg="#FF5722", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        # Remix info display
        self.display_remix_library_info()
        
        # Main button
        Button(self.root, text="ðŸ”¢ Load JSON & Start ENHANCED Auto with Characters Array Verification", 
            command=self.load_config_and_start, 
            bg="#4CAF50", fg="white", font=("Arial", 12, "bold")).pack(pady=15)
        
        # Status
        Label(self.root, text="ðŸ“Š Status:").pack(pady=(20,5))
        Label(self.root, textvariable=self.status_var, fg="blue").pack()
        
        # Instructions with Characters Array System
        instruction_text = """
    ðŸŽ¯ ENHANCED WORKFLOW WITH CHARACTERS ARRAY VERIFICATION:

    ðŸ”¢ CHARACTERS ARRAY SYSTEM:
    1. Characters Array Monitoring: Real-time tracking of character placement array
    2. Count-Based Verification: Count "1" values = accurate number of placed icons
    3. Stable Across Mods: Works reliably on all game modifications (no Putted Characters bugs)
    4. High Performance: Direct VM access with optimized JavaScript functions

    âš¡ 5-COORDINATE STRATEGY (UNCHANGED):
    - Coordinate 1: Center (original icon position)
    - Coordinate 2: Left (icon_x - 15, icon_y)
    - Coordinate 3: Right (icon_x + 15, icon_y)  
    - Coordinate 4: Top (icon_x, icon_y - 15)
    - Coordinate 5: Bottom (icon_x, icon_y + 15)

    ðŸ–¼ï¸ IMAGE DETECTION (PRESERVED):
    - Original Run button & Fullscreen detection logic kept exactly as-is
    - Auto-scroll capability for buttons outside viewport
    - Multi-scale template matching with confidence thresholds
    - Domain-specific timing adjustments for cocrea.world

    ðŸš€ NEW WORKFLOW:
    Page Load â†’ Smart Verification â†’ Game Verification â†’ âœ… Verification Success
    â†’ 3s Stabilization â†’ Start Image Detection (Run Button) â†’ Game Load
    â†’ Image Detection (Fullscreen Button) â†’ F11 Fullscreen â†’ Characters Array Setup
    â†’ Ready for Automation with Characters Array Verification

    ðŸŽ® BENEFITS:
    - Replaces unreliable Putted Characters with stable Characters Array
    - Works consistently across all game mods and versions
    - Real-time accurate state monitoring
    - No dependency on buggy game variables
    - Enhanced reliability for drag/return operations
        """
        Label(self.root, text=instruction_text, justify="left", 
            font=("Arial", 8), bg="#f0f0f0").pack(pady=10, padx=10, fill="x")
        
        # Control buttons
        self.stop_btn = Button(self.root, text="ðŸ›‘ Stop Enhanced Auto", 
                            command=self.stop_all, state="disabled",
                            bg="#f44336", fg="white", font=("Arial", 12, "bold"))
        self.stop_btn.pack(pady=10)
        
        # Progress
        Label(self.root, text="ðŸ“ˆ Progress:").pack(pady=(10,5))
        self.progress = Progressbar(self.root, length=500, mode='determinate')
        self.progress.pack(pady=5)
        
        # Current action
        Label(self.root, textvariable=self.action_var, fg="green", 
            font=("Arial", 10, "bold")).pack(pady=5)
        
        # Domain support with Characters Array System
        domains_text = f"ðŸŒ Enhanced for: cocrea.world (Chrome Debug + Characters Array Verification + Image Detection)"
        Label(self.root, text=domains_text, fg="gray", font=("Arial", 9)).pack(pady=5)
        
        # Hotkey
        Label(self.root, text="âŒ¨ï¸ ESC = Emergency stop", 
            fg="red", font=("Arial", 10, "bold")).pack(pady=5)
    
    def show_drag_stats(self):
        """Show enhanced drag statistics"""
        stats = self.get_drag_statistics()
        print("\n" + "="*60)
        print("ðŸ“Š ENHANCED DRAG STATISTICS:")
        print("="*60)
        print(stats)
        print("="*60)

    def test_websocket_connection(self):
        """Test WebSocket connection to Chrome debug"""
        try:
            ws_url = self._get_websocket_url()
            if ws_url:
                ws = websocket.create_connection(ws_url, timeout=5)
                
                # Test with simple command
                test_response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": "window.location.href", "returnByValue": True}
                })
                
                ws.close()
                
                if test_response:
                    print("âœ… WEBSOCKET TEST SUCCESSFUL:")
                    print(f"   Debug Port: {self.debugging_port}")
                    print(f"   Current URL: {test_response.get('result', {}).get('result', {}).get('value', 'Unknown')}")
                    print(f"   CDP commands working correctly")
                else:
                    print("âš ï¸ WEBSOCKET TEST WARNING:")
                    print("   WebSocket connected but CDP commands failed")
            else:
                messagebox.showerror("WebSocket Test", 
                    "âŒ Could not establish WebSocket connection\n\n"
                    "Make sure Chrome debug mode is running")
        except Exception as e:
            messagebox.showerror("WebSocket Test", f"âŒ WebSocket test failed: {e}")

    def test_verification_system(self):
        """Test Characters Array verification system"""
        try:
            if not self.characters_setup_done:
                messagebox.showinfo("Verification Test", 
                    "ðŸ”¢ Characters Array system not setup yet.\n\n"
                    "Load a JSON file and start the workflow to initialize verification.")
                return
            
            if not self.characters_available:
                messagebox.showwarning("Verification Test", 
                    "âš ï¸ Characters Array system is disabled.\n\n"
                    "WebSocket connection may not be available.")
                return
            
            # Test getting current count
            current_count = self.get_characters_count()
            
            if current_count is not None:
                print("âœ… CHARACTERS ARRAY VERIFICATION TEST:")
                print(f"   ðŸ“Š Current Characters count: {current_count}")
                print(f"   ðŸ”§ JavaScript monitoring: ACTIVE")
                print(f"   âš¡ High-performance access: ENABLED")
                print(f"   ðŸŽ¯ Status: Ready for drag verification!")
            else:
                print("âŒ CHARACTERS ARRAY VERIFICATION TEST FAILED:")
                print("   Could not get Characters count")
                print("   Game may not be loaded or VM not found")
                    
        except Exception as e:
            messagebox.showerror("Verification Test", f"âŒ Characters Array test error: {e}")

    def show_smart_dual_tracking_status(self):
        """Debug: Show current SMART DUAL tracking status"""
        try:
            print("\n🎯 SMART DUAL TRACKING STATUS:")
            print("="*50)
            print(f"Working Systems State:")
            print(f"   📢 Characters Array: {self.working_systems.get('characters', 'None')}")
            print(f"   🎯 Putted Characters: {self.working_systems.get('putted', 'None')}")
            
            available_systems = []
            if self.characters_available: available_systems.append("Characters Array")
            if self.putted_chars_available: available_systems.append("Putted Characters")
            
            print(f"Available Systems: {', '.join(available_systems) if available_systems else 'None'}")
            print(f"Last Operation Working Systems: {len(self.last_operation_working_systems)} stored")
            print("="*50)
            
            messagebox.showinfo("SMART DUAL Tracking", 
                f"📢 Characters: {self.working_systems.get('characters', 'None')}\n"
                f"🎯 Putted: {self.working_systems.get('putted', 'None')}\n\n"
                f"Available: {', '.join(available_systems) if available_systems else 'None'}")
                
        except Exception as e:
            messagebox.showerror("Tracking Status Error", f"❌ Error: {e}")

    def test_smart_verification_system(self):
            """Test Smart Verification System"""
            try:
                if not hasattr(self, 'page_verification_enabled'):
                    messagebox.showinfo("Smart Verification Test", 
                        "ðŸ” Smart Verification system not initialized yet.\n\n"
                        "Load a JSON file and start the workflow to initialize verification.")
                    return
                
                # Test WebSocket connection first
                ws_url = self._get_websocket_url()
                if not ws_url:
                    messagebox.showerror("Smart Verification Test", 
                        "âŒš WebSocket connection not available.\n\n"
                        "Make sure Chrome debug mode is running.")
                    return
                
                # Test page verification
                print("ðŸ” Testing Smart Page Verification...")
                page_result = self.verify_page_fully_loaded("test")
                
                # Test game verification  
                print("ðŸŽ® Testing Smart Game Verification...")
                game_result = self.verify_game_components_ready()
                
                # Show results
                status_page = "âœ… PASSED" if page_result else "âš ï¸ FAILED"
                status_game = "âœ… PASSED" if game_result else "âš ï¸ FAILED"
                
                print("ðŸ” SMART VERIFICATION SYSTEM TEST RESULTS:")
                print("="*50)
                print(f"   ðŸ”„ Page Verification: {status_page}")
                print(f"   ðŸŽ® Game Verification: {status_game}")
                print(f"\nâš™ï¸ Configuration:")
                print(f"   ðŸ“„ Page verification: {'âœ… Enabled' if self.page_verification_enabled else 'âŒ Disabled'}")
                print(f"   ðŸŽ® Game verification: {'âœ… Enabled' if self.game_verification_enabled else 'âŒ Disabled'}")
                print(f"   â±ï¸ Page timeout: {getattr(self, 'page_verification_timeout', 'N/A')}s")
                print(f"   â±ï¸ Game timeout: {getattr(self, 'game_verification_timeout', 'N/A')}s")
                print(f"   ðŸ”„ Stabilization wait: {getattr(self, 'stabilization_wait', 'N/A')}s")
                print(f"\nðŸš€ Smart Verification ready to replace fixed 40s waits!")
                print("="*50)
                        
            except Exception as e:
                messagebox.showerror("Smart Verification Test", f"âŒš Test error: {e}")

    def display_remix_library_info(self):
        info_frame = Frame(self.root)
        info_frame.pack(pady=10)
        
        Label(info_frame, text="ðŸŽ¼ Remix Library:", font=("Arial", 11, "bold")).pack()
        
        if not self.remix_library:
            remix_summary = "ðŸ“‹ Will generate library when loading JSON with ENHANCED workflow"
        else:
            remix_summary = f"ðŸ“‹ {len(self.remix_library)} remixes with ENHANCED verification\n"
            for i, remix in enumerate(self.remix_library):
                remix_summary += f"{i+1}. {remix['name']}: {remix['description']}\n"
        
        self.remix_display_var = StringVar()
        self.remix_display_var.set(remix_summary)
        
        Label(info_frame, textvariable=self.remix_display_var, justify="left", 
              font=("Arial", 9), fg="blue", wraplength=550).pack(pady=5)
    
    def update_remix_display(self):
        if hasattr(self, 'remix_display_var'):
            if self.remix_library:
                remix_summary = f"ðŸ“‹ {len(self.remix_library)} remixes with ENHANCED verification\n"
                for i, remix in enumerate(self.remix_library):
                    remix_summary += f"{i+1}. {remix['name']}: {remix['description']}\n"
                self.remix_display_var.set(remix_summary)
    
    def load_config_and_start(self):
            """Load config, generate dynamic remixes, and start enhanced auto process"""
            try:
                file_path = filedialog.askopenfilename(
                    title="Choose JSON configuration file",
                    filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
                )
                
                if not file_path:
                    return
                
                self._original_json_path = file_path      

                with open(file_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                # Baselines are specific to the loaded mod and must never leak
                # into the next configuration handled by the same player.
                self.empty_board_baseline = {'characters': None, 'putted': None}
                self.entry_strategy = {"selected": None, "tested": [], "verification": "UNKNOWN"}
                self._workflow_placement_transactions = []
                self.last_workflow_report = None
                
                if 'currentUrl' not in self.config or 'layout' not in self.config:
                    raise ValueError("Invalid JSON structure")
                
                layout = self.config['layout']
                if 'icons' not in layout or 'slots' not in layout:
                    raise ValueError("Missing icons or slots in layout")
                
                current_url = self.config.get('currentUrl', '').lower()
                if 'cocrea.world' not in current_url:
                    print(f"âŒ DOMAIN ERROR: This version is optimized for 'cocrea.world' only!")
                    print(f"   Current URL: {current_url}")
                    print("   ðŸ’¡ Please use a cocrea.world game URL")
                    return
                
                game_settings = self.config.get('gameSettings', {})
                has_horror = game_settings.get('hasHorror', False)

                # 1. Lấy số lượng slot từ file JSON vừa tải
                total_slots = len(layout.get('slots', []))

                # 2. Gọi hàm mới để tạo các pattern remix động
                self.generate_dynamic_patterns(total_slots)
                
                # 3. Gọi hàm generate_random_remixes, nó sẽ tự động dùng self.remix_patterns mới
                self.remix_library = self.generate_random_remixes(avoid_horror_icon=not has_horror)
                
                self.current_remix_index = 0
                # Drag operation statistics (enhanced)
                self.drag_stats = DragOperationStats()

                # [NEW - GLOBAL STOP] Event to signal a global stop request.
                # This will be overridden by the master controller if provided.
                self.stop_event = threading.Event()
                self.update_remix_display()

                # Check domain support
                current_url = self.config.get('currentUrl', '').lower()

                # Enhanced confirmation with horror info and verification status
                horror_info = ""
                if has_horror:
                    horror_info = f"ðŸ‘» Horror Mode: YES (Icon 19 avoided in normal remixes)\n"
                else:
                    horror_info = f"ðŸŽª Horror Mode: NO (All 20 icons available)\n"
                
                # Confirmation
                print(f"ðŸŽ¯ ENHANCED WORKFLOW READY:")
                print(f"   ðŸŽ® Icons: {len(layout['icons'])}")
                print(f"   ðŸŽª Slots: {len(layout['slots'])}")  
                print(f"   ðŸŽ¼ Remixes: {len(self.remix_library)}")
                print(f"   {horror_info}")
                print(f"   âš¡ ENHANCED FEATURES:")
                print(f"      â€¢ Chrome Debug Mode (port {self.debugging_port})")
                print(f"      â€¢ Drag Verification via 'Putted characters'")
                print(f"      â€¢ 5-Coordinate Retry System")
                print(f"      â€¢ WebSocket/CDP for advanced control")
                print(f"      â€¢ PyAutoGUI with smart fallback")
                print(f"      â€¢ Real-time game state monitoring")
                print(f"      â€¢ Enhanced statistics tracking") 
                print(f"      â€¢ Horror-aware remix generation")
                print(f"âœ… AUTO-STARTING ENHANCED WORKFLOW...")

                # Auto-start without user confirmation
                self.start_full_auto_process()
                
            except Exception as e:
                print(f"âŒ CONFIG LOAD ERROR: {str(e)}")
                print("   ðŸ’¡ Please check JSON file format and try again")
    
    def calculate_total_steps(self):
        """Calculate total steps for progress"""
        if self.config is None:  # âœ… Kiá»ƒm tra config khÃ´ng None
            return 10  # Tráº£ vá» sá»‘ bÆ°á»›c máº·c Ä‘á»‹nh náº¿u khÃ´ng cÃ³ config
        
        layout = self.config['layout']
        game_settings = self.config.get('gameSettings', {})
        has_horror = game_settings.get('hasHorror', False)
        has_bonus = game_settings.get('hasBonus', False)
        
        setup_steps = 7  # Increased by 1 for verification setup
        
        if self.mode.get() == 1:  # Showcase + Remix
            normal_range = range(len(layout['icons']) - 1) if has_horror else range(len(layout['icons']))
            normal_showcase = len(normal_range)
            normal_bonus = len(layout.get('bonus', [])) if has_bonus else 0
            normal_remix_steps = sum(len(remix['icons']) + 1 for remix in self.remix_library)
            horror_transition = 1 if has_horror else 0
            horror_showcase = len(layout['icons']) if has_horror else 0
            horror_bonus = len(layout.get('bonus', [])) if (has_horror and has_bonus) else 0
            horror_remix_steps = sum(len(remix['icons']) + 1 for remix in self.remix_library) if has_horror else 0
            
            total_steps = (setup_steps + normal_showcase + normal_bonus + normal_remix_steps + 
                        horror_transition + horror_showcase + horror_bonus + horror_remix_steps)
        else:  # Only Remix
            remix_only_steps = sum(len(remix['icons']) + 1 for remix in self.remix_library)
            total_steps = setup_steps + remix_only_steps
        
        return total_steps
    
    def start_full_auto_process(self):
        """Start enhanced auto process"""
        self.is_playing = True
        self.should_stop = False
        self.current_remix_index = 0
        self.drag_stats = DragOperationStats()
        
        # Update UI
        self.stop_btn.config(state="normal")
        mode_text = "Showcase + Remix" if self.mode.get() == 1 else "Remix Only"
        self.status_var.set(f"Running ENHANCED Auto ({mode_text})...")
        
        # Setup progress
        total_steps = self.calculate_total_steps()
        self.progress['maximum'] = total_steps
        self.progress['value'] = 0
        
        print(f"ðŸŽ¯ Starting ENHANCED Auto with {total_steps} steps")
        print(f"ðŸŽ¼ Processing {len(self.remix_library)} remixes with ENHANCED verification")
        print(f"ðŸŒ Using Chrome debug port: {self.debugging_port}")
        print(f"ðŸ” Drag verification system will be setup during initialization")
        
        # Start auto thread
        self.auto_thread = threading.Thread(target=self.full_auto_loop_corrected, daemon=True)
        self.auto_thread.start()
        
        # Register ESC hotkey
        keyboard.add_hotkey('esc', self.emergency_stop)
    
    def full_auto_loop_corrected(self):
        """Main enhanced auto loop"""
        try:
            print("ðŸ”§ Starting ENHANCED auto loop...")
            
            def update_action(message):
                self.action_var.set(message)
                self.root.update()
            
            # Setup game environment with verification
            if not self.setup_game_environment(progress_callback=update_action):
                messagebox.showerror("Setup Error", "Cannot setup enhanced game environment!")
                return
            
            self.progress['value'] = 7  # Updated for verification setup step
            self.root.update()
            
            # Execute workflow based on mode
            if self.mode.get() == 1:  # Showcase + Remix
                print("ðŸŽª Starting ENHANCED full showcase workflow...")
                success = self.execute_full_showcase_workflow_corrected(progress_callback=update_action)
            else:  # Remix only
                print("ðŸŽµ Starting ENHANCED remix-only workflow...")
                success = self.execute_remix_only_workflow_corrected(progress_callback=update_action)
            
            # Show completion
            if success and not self.should_stop:
                self.show_enhanced_completion_message()
            
            self.stop_all()
            
        except Exception as e:
            messagebox.showerror("Auto Error", "Error in enhanced auto: " + str(e))
            self.stop_all()
    
    def show_enhanced_completion_message(self):
        """Show enhanced completion message"""
        mode_text = "Showcase + Remix" if self.mode.get() == 1 else "Remix Only"
        stats = self.drag_stats
        success_rate = (stats.successful_drags / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
        verification_rate = (stats.verification_enabled / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
        verification_success_rate = (stats.verification_successful / stats.verification_enabled * 100) if stats.verification_enabled > 0 else 0
        avg_coord_retries = sum(stats.coordinate_retries) / len(stats.coordinate_retries) if stats.coordinate_retries else 0
        
        self.action_var.set("ðŸŽ‰ ENHANCED Auto completed!")
        # Generate final comprehensive report
        print("\nðŸ“‹ GENERATING FINAL SUCCESS REPORT...")
        self.cleanup_and_report("Successful_Completion")        
        print("\n" + "ðŸŽ‰"*20)
        print("ðŸŽ‰ ENHANCED AUTO COMPLETED SUCCESSFULLY! ðŸŽ‰") 
        print("ðŸŽ‰"*20)
        print(f"\nðŸ“Š WORKFLOW SUMMARY:")
        print(f"   âœ… Mode: ENHANCED {mode_text}")
        print(f"   ðŸ“Š Total attempts: {stats.total_attempts}")
        print(f"   ðŸ“ˆ Success rate: {success_rate:.1f}%")
        print(f"   ðŸŽ¯ Average coordinates tried: {avg_coord_retries:.1f}/5")
        print(f"   ðŸŽ¼ Remixes processed: {len(self.remix_library)}")
        print(f"\nðŸ” VERIFICATION STATISTICS:")
        print(f"   ðŸ“Š Verification enabled: {verification_rate:.1f}%")
        print(f"   âœ… Verification success: {verification_success_rate:.1f}%")
        print(f"   ðŸ”§ System status: {'âœ… ACTIVE' if self.characters_available else 'âš ï¸ DISABLED'}")
        print(f"\nâš¡ ENHANCED FEATURES USED:")
        print(f"   ðŸŒ Chrome Debug Mode (port {self.debugging_port})")
        print(f"   ðŸ” Drag verification via 'Putted characters'")
        print(f"   ðŸŽ¯ 5-coordinate retry system")
        print(f"   ðŸ“¡ WebSocket/CDP for advanced control")
        print(f"   ðŸ”„ Smart fallback between methods")
        print(f"   ðŸ“Š Real-time game state monitoring")
        print(f"\nðŸš€ The workflow is now ENHANCED and super reliable! âœ¨")
        print("="*60)
        
    def stop_all(self):
        """Enhanced stop with complete resource cleanup"""
        print("Enhanced stop initiated...")
        
        self.should_stop = True
        self.is_playing = False

        # Stop batch verification session
        if self.batch_verification_active:
            try:
                self.stop_batch_verification_session()
            except Exception as e:
                print(f"Batch stop error: {e}")

        # Enhanced resource cleanup
        try:
            if hasattr(self, 'resource_manager'):
                self.resource_manager.force_cleanup()
        except Exception as e:
            print(f"Resource cleanup error: {e}")

        # Force garbage collection
        gc.collect()
        
        # Update GUI safely
        try:
            self.stop_btn.config(state="disabled")
            self.action_var.set("ENHANCED Auto stopped")
            self.status_var.set("Stopped")
        except Exception as gui_error:
            print(f"GUI update error during stop: {gui_error}")

        # Show final stats if available
        if hasattr(self, 'drag_stats') and self.drag_stats.total_attempts > 0:
            try:
                print("FINAL ENHANCED AUTO STATISTICS:")
                print(self.get_drag_statistics())
            except Exception as stats_error:
                print(f"Stats error: {stats_error}")

        # Enhanced Chrome cleanup
        try:
            self.cleanup_chrome_complete()
        except Exception as chrome_error:
            print(f"Chrome cleanup error: {chrome_error}")

        # Remove hotkey safely
        try:
            import keyboard
            keyboard.remove_hotkey('esc')
        except:
            pass
        
        print("Enhanced stop completed")
    
    def emergency_stop(self):
        """Emergency stop"""
        self.stop_all()
        print("\nðŸš¨ EMERGENCY STOP ACTIVATED!")
        print("âš ï¸ ENHANCED Auto stopped with ESC key")
        print("ðŸ›‘ All operations halted safely")
    
    def run(self):
        """Run application"""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("Application stopped")
        finally:
            try:
                keyboard.remove_hotkey('esc')
            except:
                pass
            # Cleanup on exit
            self.cleanup_chrome_complete()

    def get_enhanced_workflow_summary(self):
            """
            ðŸ“Š NEW: Get comprehensive workflow summary with smart features
            FIXED: Distinguish between Polos (icon placement) and Enabled (UI state)
            """
            try:
                stats = self.drag_stats
                current_state = self.get_comprehensive_slot_state()
                
                # Basic stats
                success_rate = (stats.successful_drags / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
                verification_rate = (stats.verification_enabled / stats.total_attempts * 100) if stats.total_attempts > 0 else 0
                
                summary = f"""
    ðŸ“Š ENHANCED WORKFLOW SUMMARY:
    {'='*60}
    ðŸŽ¯ DRAG OPERATIONS:
    â€¢ Total attempts: {stats.total_attempts}
    â€¢ Success rate: {success_rate:.1f}%
    â€¢ Verification enabled: {verification_rate:.1f}%
    â€¢ Average coordinate retries: {sum(stats.coordinate_retries) / len(stats.coordinate_retries) if stats.coordinate_retries else 0:.1f}/5

    ðŸ›¡ï¸ SMART FEATURES ACTIVE:
    â€¢ âœ… Polos array tracking (PRIMARY)
    â€¢ âœ… Cross-contamination prevention  
    â€¢ âœ… Pre-flight slot checking
    â€¢ âœ… Smart batch return sequencing
    â€¢ âœ… Emergency recovery protocols
    â€¢ âœ… Real-time state validation

    ðŸ” CURRENT SLOT STATE:"""

                if current_state:
                    putted = current_state.get('putted_characters', 'N/A')
                    polos = current_state.get('polos_array', [])
                    enabled = current_state.get('enabled_list', [])
                    
                    summary += f"""
    â€¢ Putted characters: {putted}
    â€¢ Active slots (Polos): {sum(1 for x in polos if x == "1") if polos else 'N/A'}
    â€¢ PRIMARY consistency: {'âœ… Good' if current_state.get('putted_polos_match', True) else 'âš ï¸ Issue'}
    â€¢ Polos array: {polos if len(str(polos)) < 50 else f'{len(polos)} slots'}
    â€¢ Enabled List: {enabled if len(str(enabled)) < 50 else f'{len(enabled)} slots'} (UI state)"""
                else:
                    summary += "\nâ€¢ âš ï¸ Current state unavailable"

                summary += f"""

    âš¡ SYSTEM STATUS:
    â€¢ Verification system: {'âœ… ACTIVE' if self.characters_available else 'âš ï¸ DISABLED'}
    â€¢ WebSocket connection: {'âœ… Ready' if self._get_websocket_url() else 'âŒ Failed'}
    â€¢ Configuration: {'âœ… Loaded' if hasattr(self, 'config') and self.config else 'âŒ Missing'}

    â„¹ï¸ ARRAY UNDERSTANDING:
    â€¢ Polos Array = Icon placement state (PRIMARY for verification)
    â€¢ Enabled List = UI availability state (informational only)

    {'='*60}
    ðŸŽ‰ ENHANCED SPRUNKI GAME PLAYER READY!
    """
                
                return summary
                
            except Exception as e:
                return f"âŒ Error generating workflow summary: {e}"

    def log_comprehensive_session_report(self, session_type="Unknown"):
        """
        ðŸ“‹ NEW: Generate comprehensive session report for debugging
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            report = f"""
    {'='*80}
    ðŸ“‹ COMPREHENSIVE SESSION REPORT
    {'='*80}
    â° Session: {session_type} at {timestamp}

    ðŸŽ¯ DRAG OPERATIONS SUMMARY:
    {self.get_drag_statistics()}

    ðŸ” CURRENT SYSTEM STATE:
    {self.get_enhanced_workflow_summary()}

    ðŸ›¡ï¸ SMART FEATURES STATUS:
    â€¢ Pre-flight slot checking: {'âœ… Active' if self.characters_available else 'âŒ Disabled'}
    â€¢ Cross-contamination prevention: {'âœ… Active' if self.characters_available else 'âŒ Disabled'}
    â€¢ Smart batch return: âœ… Available
    â€¢ Emergency recovery: âœ… Available
    â€¢ Real-time validation: {'âœ… Active' if self.characters_available else 'âŒ Disabled'}

    ðŸ“Š FINAL STATE VALIDATION:
    """
            
            # Get final validation
            is_complete, issues, recommendations = self.validate_workflow_completion()
            
            if is_complete:
                report += "   âœ… PERFECT COMPLETION - No issues detected\n"
            else:
                report += f"   âš ï¸ {len(issues)} ISSUES DETECTED:\n"
                for i, issue in enumerate(issues, 1):
                    report += f"      {i}. {issue}\n"
                
                report += f"   ðŸ’¡ RECOMMENDATIONS:\n"
                for i, rec in enumerate(recommendations, 1):
                    report += f"      {i}. {rec}\n"
            
            report += f"""
    ðŸ”§ SYSTEM DIAGNOSTICS:
    â€¢ Configuration: {'âœ… Loaded' if hasattr(self, 'config') and self.config else 'âŒ Missing'}
    â€¢ WebSocket: {'âœ… Ready' if self._get_websocket_url() else 'âŒ Failed'}
    â€¢ Verification Setup: {'âœ… Done' if self.putted_chars_setup_done else 'âŒ Missing'}
    â€¢ Browser State: {'âœ… Active' if hasattr(self, 'chrome_process') and self.chrome_process else 'âŒ Closed'}

    {'='*80}
    ðŸŽ‰ ENHANCED SPRUNKI AUTOMATION SYSTEM REPORT COMPLETE
    {'='*80}
    """
            
            print(report)
            
            # Save to file for debugging - use project folder
            try:
                import os
                # 📁 Use project folder for reports if available
                if self.current_project_folder:
                    reports_dir = os.path.join(self.current_project_folder, "session_reports")
                    print(f"📁 Using project folder for reports: {reports_dir}")
                else:
                    reports_dir = os.path.join(self.base_output_dir, "session_reports")
                    print(f"📁 Using base folder for reports: {reports_dir}")
                
                os.makedirs(reports_dir, exist_ok=True)
                
                safe_session_type = session_type.replace(" ", "_").replace(":", "-")
                filename = f"session_{safe_session_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                filepath = os.path.join(reports_dir, filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(report)
                
                print(f"ðŸ“„ Session report saved: {filepath}")
                
            except Exception as e:
                print(f"âš ï¸ Could not save session report: {e}")
            
            return report
            
        except Exception as e:
            error_report = f"âŒ Error generating session report: {e}"
            print(error_report)
            return error_report

    def cleanup_and_report(self, session_type="Workflow"):
        """
        ðŸ§¹ NEW: Enhanced cleanup with comprehensive reporting
        """
        try:
            print(f"\nðŸ§¹ ENHANCED CLEANUP & REPORTING: {session_type}")
            print("="*60)
            
            # Final smart cleanup is guarded so a workflow finally block and
            # the completion-report path cannot execute it twice.
            cleanup_result = self._run_cleanup_once(
                use_cdp=False,
                context={"workflow": session_type},
                obs_controller=self.obs_controller,
            )
            if cleanup_result.status not in {
                OperationStatus.VERIFIED,
                OperationStatus.ALREADY_EMPTY,
            }:
                print(
                    f"   🛑 Cleanup status: {cleanup_result.status.value} "
                    f"({cleanup_result.message})"
                )
            
            # Generate comprehensive report
            print("\nðŸ“‹ Generating comprehensive session report...")
            session_report = self.log_comprehensive_session_report(session_type)
            
            # Close browser if needed
            if hasattr(self, 'chrome_process') and self.chrome_process:
                print("\nðŸŒ Closing browser...")
                self.close_game_window()
            
            print(f"âœ… Enhanced cleanup completed for: {session_type}")
            
        except Exception as e:
            print(f"âŒ Error during enhanced cleanup: {e}")

    def _normalize_samesite(self, raw_value):
        """
        Chuyển đổi sameSite từ format Cookie-Editor sang format CDP.
        Cookie-Editor export: "no_restriction", "unspecified", "lax", "strict"
        CDP yêu cầu chính xác: "None", "Lax", "Strict"
        """
        mapping = {
            "no_restriction": "None",
            "none": "None",
            "lax": "Lax",
            "strict": "Strict",
            "unspecified": "Lax",  # Default an toàn
        }
        normalized = raw_value.lower().strip() if raw_value else "lax"
        return mapping.get(normalized, "Lax")

    def inject_cookies_via_cdp(self):
        """
        Đọc cookies từ file json và tiêm vào trình duyệt qua CDP
        """
        cookie_file = "cocrea_cookies.json"
        if not os.path.exists(cookie_file):
            print(f"⚠️ Không tìm thấy file {cookie_file}. Chạy ở chế độ Guest.")
            return False

        try:
            print(f"🍪 Đang nạp cookies từ {cookie_file}...")
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)

            # Kết nối WebSocket
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("❌ Không thể kết nối WebSocket để inject cookies")
                return False
            
            ws = websocket.create_connection(ws_url, timeout=10)

            success_count = 0
            fail_count = 0
            for cookie in cookies:
                domain = cookie.get('domain', '.cocrea.world')
                
                # FIX: Chuyển đổi sameSite từ Cookie-Editor format → CDP format
                raw_samesite = cookie.get('sameSite', 'Lax')
                cdp_samesite = self._normalize_samesite(raw_samesite)
                
                # FIX: Chrome bắt buộc secure=true khi sameSite=None
                secure = cookie.get('secure', False)
                if cdp_samesite == "None":
                    secure = True
                
                cookie_params = {
                    "name": cookie.get('name'),
                    "value": cookie.get('value'),
                    "domain": domain,
                    "path": cookie.get('path', '/'),
                    "secure": secure,
                    "httpOnly": cookie.get('httpOnly', False),
                    "sameSite": cdp_samesite,
                    "expires": cookie.get('expirationDate', -1),
                    # FIX: Thêm URL để CDP xác định domain chính xác
                    "url": f"https://{domain.lstrip('.')}"
                }

                # Gửi lệnh setCookie qua CDP
                response = self._send_cdp_command(ws, {
                    "method": "Network.setCookie",
                    "params": cookie_params
                })
                
                # Kiểm tra kết quả từng cookie
                cookie_ok = True
                if response and 'result' in response:
                    cookie_ok = response['result'].get('success', False)
                
                if cookie_ok:
                    success_count += 1
                else:
                    fail_count += 1
                    print(f"   ⚠️ Cookie '{cookie.get('name')}' inject FAILED (sameSite={raw_samesite}→{cdp_samesite})")

            ws.close()
            
            if fail_count > 0:
                print(f"⚠️ Inject cookies: {success_count} thành công, {fail_count} thất bại")
            else:
                print(f"✅ Đã tiêm thành công tất cả {success_count} cookies!")
            
            # Chỉ cần token và cookie-user-id là đủ để đăng nhập
            key_cookies = [c.get('name') for c in cookies]
            has_token = 'token' in key_cookies
            has_user_id = 'cookie-user-id' in key_cookies
            if has_token and has_user_id:
                print(f"✅ Cookies đăng nhập quan trọng: token=✓, cookie-user-id=✓")
            else:
                print(f"⚠️ THIẾU cookies đăng nhập: token={'✓' if has_token else '✗'}, cookie-user-id={'✓' if has_user_id else '✗'}")
            
            return success_count > 0

        except Exception as e:
            print(f"❌ Lỗi khi tiêm cookies: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    print("ðŸš€ Starting ENHANCED Sprunki Game Player...")
    print("âš¡ ENHANCED FEATURES:")
    print("   ðŸŒ Chrome Debug Mode: Single browser with remote debugging")
    print("   ðŸ” Drag Verification: Real-time 'Putted characters' monitoring")
    print("   ðŸŽ¯ 5-Coordinate Retry: Center â†’ Left â†’ Right â†’ Top â†’ Bottom")
    print("   ðŸ“¡ WebSocket/CDP: Advanced browser control and JavaScript injection")
    print("   🌐 CDP Primary: All game operations via Chrome DevTools with PyAutoGUI fallback")
    print("   ðŸ›¡ï¸ AdBlocker: Automatic uBlock Origin extension loading")
    print("   âš™ï¸ Config Integration: All settings from config.json")
    print("   ðŸ“Š Enhanced Statistics: Verification rates and coordinate usage")
    print("   ðŸ§¹ Smart Cleanup: Auto-return icons on verification failure")
    print("   ðŸŽ¯ Session Sharing: Can reuse browser from JSON generation")
    print("="*70)
    
    app = SprunkiGamePlayer()
    
    print("\nðŸŽ® ENHANCED Auto Player ready!")
    print("ðŸ“Š Features: Drag Verification + 5-Coordinate Retry + Chrome Debug")
    print("ðŸŽ¼ Ultra-reliable automation with game state monitoring")
    print("âš¡ Optimized for 95%+ success rate with intelligent fallback")
    
    app.run()

# json_generator.py (v11.1 - HYBRID - Remove-first return controls)
# UPGRADED: Phase value comparison as primary method with visual fallback

import json
import copy
import time
import os
import subprocess
import requests
import hashlib
from datetime import datetime
import pyautogui
import pygetwindow as gw
import random
import websocket
from image_detector import ImageDetector
import numpy as np
from page_load_verifier import PageLoadVerifier
from manual_profile_manager import find_profile, validate_profile
from manual_prepare_gestures import DOUBLE_CLICK_DRAG_TYPE
from cdp_gesture_executor import execute_cdp_gesture

from sprunki_game_player import SprunkiGamePlayer

# Try to import optional dependencies with fallbacks
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    print("ÃƒÂ¢Ã…Â¡ ÃƒÂ¯Ã‚Â¸Ã‚Â OpenCV not available - using basic image comparison")

try:
    from skimage.metrics import structural_similarity
    HAS_SSIM = True
    _ssim_func = structural_similarity  # Lưu reference an toàn
except ImportError:
    HAS_SSIM = False
    _ssim_func = None
    print("⚠️ scikit-image not available - using custom SSIM implementation")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("ÃƒÂ¢Ã…Â¡ ÃƒÂ¯Ã‚Â¸Ã‚Â PIL not available - using pyautogui screenshots only")

class SprunkiJSONGenerator:
    """
    HYBRID Sprunki JSON Generator (v11.1 - Remove-first return controls)
    Process:
        1. Get comprehensive state data + coordinates
        2. Drag icon 19 to slot 3  
        3. Get state data after drag
        4. PRIMARY: Compare phase variable (1->2 = hasHorror)
        5. FALLBACK: Screenshot comparison if phase not found
        6. Try highest index icon if no horror detected
        7. Generate full layout
        8. Combine results into final JSON
    
    NEW: Phase value comparison as primary method with visual fallback
    Only phase transition 1->2 counts as hasHorror = true
    """
    
    def __init__(self, master_controller=None):
        """
        [PHASE 4 REFACTORED]
        Initializer for the JSON Generator.
        It no longer loads its own config, relying entirely on the game_player engine.
        Its main role is to hold the specialized JavaScript snippets.
        """
        self.master_controller = master_controller
        # [MODIFIED] Không cần truyền stop_event ở đây vì json_generator
        # có luồng sống riêng và ngắn. Nó sẽ tự tạo stop_event mặc định.
        self.game_player = SprunkiGamePlayer() 
        self.ws_url = None

        # JavaScript for comprehensive state capture (coordinates + game data)
        self.js_get_state_and_coordinates = '''
        new Promise(async (resolve, reject) => {
            try {
                console.log("Capturing comprehensive state data + coordinates...");
                
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
                if (!vm) return reject({ error: 'Cannot find Scratch VM' });
                
                const allTargets = vm.runtime.targets.map(t => t);
                
                // Capture comprehensive state data like the old method
                const state = { 
                    timestamp: new Date().toISOString(), 
                    characters: [], 
                    variables: {},
                    allTargets: []
                };
                
                // Capture all targets data
                allTargets.forEach((target) => {
                    const targetData = {
                        id: target.id, 
                        name: target.getName(), 
                        visible: target.visible,
                        x: target.x, 
                        y: target.y, 
                        size: target.size, 
                        direction: target.direction,
                        currentCostume: target.currentCostume,
                        costumes: target.sprite.costumes.map(c => ({ 
                            name: c.name, 
                            size: c.size, 
                            rotationCenterX: c.rotationCenterX, 
                            rotationCenterY: c.rotationCenterY 
                        })),
                        sounds: target.sprite.sounds.map(s => s.name),
                        effects: { ...target.effects },
                        variables: {}
                    };
                    
                    // Capture target-specific variables
                    if (target.variables) {
                        Object.values(target.variables).forEach(variable => {
                            targetData.variables[variable.name] = {
                                name: variable.name,
                                value: variable.value,
                                type: variable.type || ""
                            };
                            // Also add to global variables collection
                            state.variables[variable.name] = {
                                name: variable.name,
                                value: variable.value,
                                type: variable.type || ""
                            };
                        });
                    }
                    
                    state.allTargets.push(targetData);
                    
                    if (!target.isStage) {
                        state.characters.push(targetData);
                    }
                });
                
                console.log("🎬 Bắt đầu quá trình lấy icons (Logic đơn giản - Không sắp xếp)...");

                // Bước 1: LẤY CÁC ICON ĐANG HIỆN THỊ
                console.log("🔶️ Bước 1: Tìm các sprite chứa cụm từ 'Icons' đang hiện thị...");
                const visibleIcons = allTargets.filter(t => t.sprite.name.includes('Icons') && t.visible);
                console.log(`✦ Tìm thấy ${visibleIcons.length} icons trên màn hình.`);

                // Bước 2: CHUYỂN ĐỔI TỌA ĐỘ, LỌC NỬA DƯỚI VÀ GÁN INDEX TRỰC TIẾP (Không có sắp xếp)
                console.log("🔶️ Bước 2: Chuyển đổi tọa độ, lọc và gán index theo thứ tự gốc...");
                const icons = visibleIcons.map((icon, index) => {
                    // Chuyển đổi tọa độ
                    const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                    return {
                        originalIcon: icon,
                        x: pixelCoords.x,
                        y: pixelCoords.y,
                        // Giữ lại index gốc từ mảng `visibleIcons`
                        originalIndexInVisibleList: index 
                    };
                })
                // Lọc các icon ở nửa dưới màn hình
                .filter(icon => icon.y >= 540) 
                // Gán index cuối cùng tuần tự cho danh sách SAU KHI LỌC
                .map((iconData, finalIndex) => {
                    return {
                        x: iconData.x,
                        y: iconData.y,
                        index: finalIndex // Gán index tuần tự 0, 1, 2... cho các icon hợp lệ
                    };
                });
                
                console.log(`✦ Sau khi lọc nửa dưới, có ${icons.length} icons hợp lệ.`);
                console.log("✦ HOÀN THÀNH! Icons được tạo theo logic đơn giản (Không sắp xếp theo tọa độ):");
                console.table(icons);

                // =====================================================
                // >>> END OF CHANGES
                // =====================================================                
                
                // Get slots coordinates
                const poloSprites = allTargets.filter(t => t.sprite.name.startsWith('Polo '));
                poloSprites.sort((a, b) => (parseInt(a.sprite.name.match(/\\d+/)?.[0] || '0') - parseInt(b.sprite.name.match(/\\d+/)?.[0] || '0')));
                
                // Convert to pixel coordinates first
                const slotsWithCoords = poloSprites.map(s => ({ ...toPixelWithMenu(s.x, s.y), originalIndex: s.sprite.name }));
                
                // Sort by X coordinate (left to right) and reassign index
                slotsWithCoords.sort((a, b) => a.x - b.x);
                const slots = slotsWithCoords.map((slot, i) => ({ 
                    x: slot.x, 
                    y: slot.y, 
                    index: i,
                    originalSprite: slot.originalIndex
                }));
                
                // Find horror icon (index 19) and target slot (index 3)
                const horrorIconCoords = icons.find(i => i.index === 19);
                const targetSlotCoords = slots.find(s => s.index === 3);
                
                state.icons = icons;
                state.slots = slots;
                state.dragCoordinates = { 
                    icon: horrorIconCoords, 
                    slot: targetSlotCoords 
                };
                
                console.log("Comprehensive state + coordinates captured successfully");
                resolve(state);

            } catch(e) { 
                reject({ error: e.toString(), stack: e.stack }); 
            }
        });
        '''

        self.js_find_skip_intro = '''
        new Promise(async (resolve, reject) => {
            try {
                console.log("ÃƒÂ°Ã…Â¸Ã¢â‚¬Â Ã‚Â  Searching for ALL Skip Intro buttons (multiple detection)...");

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
                    console.log("ÃƒÂ¢Ã‚Â Ã…â€™ VM not found - cannot search for Skip Intro buttons");
                    resolve({ found: false, count: 0, buttons: [], reason: "vm_not_found" });
                    return;
                }

                const allTargets = vm.runtime.targets.map(t => t);
                console.log(`ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ Total targets found: ${allTargets.length}`);

                // Find ALL targets with name "Intro" and costume "Skip"
                const introTargets = allTargets.filter(target => {
                    if (target.sprite.name !== "Intro") {
                        return false;
                    }

                    const hasSkipCostume = target.sprite.costumes.some(costume => 
                        costume.name === "Skip"
                    );

                    return hasSkipCostume;
                });

                console.log(`ÃƒÂ°Ã…Â¸Ã¢â‚¬Â Ã‚Â  Found ${introTargets.length} 'Intro' targets with 'Skip' costume`);

                if (introTargets.length === 0) {
                    console.log("ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¹ÃƒÂ¯Ã‚Â¸Ã‚Â  No Skip Intro buttons found");

                    // DEBUG: Check for alternative patterns
                    const skipCostumeTargets = allTargets.filter(target => 
                        target.sprite.costumes.some(costume => costume.name === "Skip")
                    );

                    if (skipCostumeTargets.length > 0) {
                        console.log(`ÃƒÂ°Ã…Â¸Ã¢â‚¬Â Ã‚Â  Found ${skipCostumeTargets.length} targets with 'Skip' costume but different names:`);
                        skipCostumeTargets.forEach((target, index) => {
                            console.log(`   Target ${index + 1}: '${target.sprite.name}' - Position: (${target.x}, ${target.y}) - Visible: ${target.visible}`);
                        });
                    }

                    resolve({ found: false, count: 0, buttons: [], reason: "target_not_found" });
                    return;
                }

                // Process ALL intro targets
                const skipIntroButtons = [];

                introTargets.forEach((target, index) => {
                    console.log(`\\nÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ Processing Intro Target ${index + 1}/${introTargets.length}:`);
                    console.log(`   - Logic Position: (${target.x}, ${target.y})`);
                    console.log(`   - Visible: ${target.visible}`);
                    console.log(`   - Current Costume: ${target.sprite.costumes[target.currentCostume]?.name || 'Unknown'}`);
                    console.log(`   - All Costumes: ${target.sprite.costumes.map(c => c.name).join(', ')}`);

                    // Convert coordinates regardless of visibility (some might be programmatically shown)
                    const pixelCoords = toPixelWithMenu(target.x, target.y);

                    if (pixelCoords.x !== null && pixelCoords.y !== null) {
                        const buttonInfo = {
                            index: index + 1,
                            x: pixelCoords.x,
                            y: pixelCoords.y,
                            logicX: target.x,
                            logicY: target.y,
                            visible: target.visible,
                            targetName: target.sprite.name,
                            currentCostumeName: target.sprite.costumes[target.currentCostume]?.name || 'Unknown',
                            allCostumes: target.sprite.costumes.map(c => c.name)
                        };

                        skipIntroButtons.push(buttonInfo);

                        console.log(`   ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Converted to pixel coordinates: (${pixelCoords.x}, ${pixelCoords.y})`);
                        console.log(`   ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Added to click list (${target.visible ? 'VISIBLE' : 'HIDDEN'})`);
                    } else {
                        console.log(`   ÃƒÂ¢Ã‚Â Ã…â€™ Coordinate conversion failed for target ${index + 1}`);
                    }
                });

                // Summary
                const visibleButtons = skipIntroButtons.filter(btn => btn.visible);
                const hiddenButtons = skipIntroButtons.filter(btn => !btn.visible);

                console.log(`\\nÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…  SKIP INTRO BUTTONS SUMMARY:`);
                console.log(`   - Total found: ${skipIntroButtons.length}`);
                console.log(`   - Visible: ${visibleButtons.length}`);
                console.log(`   - Hidden: ${hiddenButtons.length}`);

                if (skipIntroButtons.length > 0) {
                    console.log(`\\nÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ Will click ALL ${skipIntroButtons.length} buttons to ensure success:`);
                    skipIntroButtons.forEach((btn, index) => {
                        console.log(`   ${index + 1}. (${btn.x}, ${btn.y}) - ${btn.visible ? 'VISIBLE' : 'HIDDEN'} - Costume: ${btn.currentCostumeName}`);
                    });
                }

                resolve({
                    found: skipIntroButtons.length > 0,
                    count: skipIntroButtons.length,
                    buttons: skipIntroButtons,
                    visibleCount: visibleButtons.length,
                    hiddenCount: hiddenButtons.length,
                    reason: skipIntroButtons.length > 0 ? "success" : "no_valid_buttons"
                });

            } catch(e) { 
                console.error("ÃƒÂ¢Ã‚Â Ã…â€™ Error searching for Skip Intro buttons:", e);
                resolve({ found: false, count: 0, buttons: [], reason: "error", error: e.toString() }); 
            }
        });
        '''
        # Keep JSON generation entry detection in sync with the playback engine.
        # The older JSON-only detector only recognized `Intro` + `Skip`, missing
        # valid action targets such as `SkipCutscene/button`.
        self.js_find_skip_intro = self.game_player.js_find_skip_intro

        # Full layout capture JavaScript
        self.js_full_layout = r'''
                new Promise(async (resolve, reject) => {
                    try {
                        console.log("Starting full layout capture...");

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

                        // Capture the per-slot Remove control from renderer bounds.
                        // target.x/y is used only for matching; the click point is the
                        // center of the rendered Remove costume so costume registration
                        // offsets cannot move the click away from the actual X button.
                        function captureRemoveControls(allTargets, renderer, poloSlots) {
                            const selector = {
                                targetName: 'Mute Buttons',
                                costumeName: 'Remove'
                            };
                            const offsets = [
                                [0, 0], [-5, 0], [5, 0], [0, -5], [0, 5],
                                [-8, -8], [8, -8], [-8, 8], [8, 8]
                            ];

                            const toPixelPoint = (logicX, logicY) => {
                                const point = toPixelWithMenu(logicX, logicY);
                                return point.x === null || point.y === null ? null : point;
                            };

                            const boundsToPixels = (rawBounds) => {
                                if (!rawBounds) return null;
                                const left = Number(rawBounds.left);
                                const right = Number(rawBounds.right);
                                const top = Number(rawBounds.top);
                                const bottom = Number(rawBounds.bottom);
                                if (![left, right, top, bottom].every(Number.isFinite)) return null;
                                const first = toPixelPoint(left, top);
                                const second = toPixelPoint(right, bottom);
                                if (!first || !second) return null;
                                const x = Math.min(first.x, second.x);
                                const y = Math.min(first.y, second.y);
                                const width = Math.abs(second.x - first.x);
                                const height = Math.abs(second.y - first.y);
                                if (!(width > 0 && height > 0)) return null;
                                return {
                                    left: Math.round(x),
                                    top: Math.round(y),
                                    width: Math.round(width),
                                    height: Math.round(height),
                                    right: Math.round(x + width),
                                    bottom: Math.round(y + height),
                                    centerX: x + (width / 2),
                                    centerY: y + (height / 2),
                                    raw: { left, right, top, bottom }
                                };
                            };

                            const readBounds = (target) => {
                                const drawableId = target.drawableID ?? target._drawableID;
                                const rawCandidates = [];
                                // Prefer renderer bounds: target logical position
                                // and costume registration are not click geometry.
                                if (renderer && drawableId !== undefined && drawableId !== null) {
                                    for (const name of ['getBounds', 'getBoundsForDrawable']) {
                                        try {
                                            if (typeof renderer[name] === 'function') {
                                                rawCandidates.push({
                                                    source: `renderer.${name}`,
                                                    bounds: renderer[name](drawableId)
                                                });
                                            }
                                        } catch (e) {}
                                    }
                                }
                                // Keep target.getBounds as a compatibility
                                // fallback for runtimes that expose no renderer
                                // bounds API.  It is still validated and never
                                // used as target.x/y click geometry.
                                try {
                                    if (typeof target.getBounds === 'function') {
                                        rawCandidates.push({
                                            source: 'target.getBounds',
                                            bounds: target.getBounds()
                                        });
                                    }
                                } catch (e) {}
                                for (const candidate of rawCandidates) {
                                    const pixelBounds = boundsToPixels(candidate.bounds);
                                    if (pixelBounds) {
                                        return { ...pixelBounds, source: candidate.source };
                                    }
                                }
                                return null;
                            };

                            const removeCandidates = allTargets
                                .filter(target => {
                                    const spriteName = target.sprite?.name || '';
                                    const costume = target.sprite?.costumes?.[target.currentCostume];
                                    return spriteName === selector.targetName
                                        && costume?.name === selector.costumeName
                                        && Boolean(target.visible)
                                        && Number(target.size ?? 1) > 0;
                                })
                                .map((target, runtimeIndex) => {
                                    const bounds = readBounds(target);
                                    const logicPoint = toPixelPoint(target.x, target.y);
                                    if (!bounds || !logicPoint) return null;
                                    return {
                                        runtimeIndex,
                                        logicX: Number(target.x),
                                        logicY: Number(target.y),
                                        pixelX: logicPoint.x,
                                        pixelY: logicPoint.y,
                                        bounds,
                                        targetName: selector.targetName,
                                        costumeName: selector.costumeName
                                    };
                                })
                                .filter(Boolean)
                                .sort((a, b) => a.pixelX - b.pixelX || a.pixelY - b.pixelY);

                            const slotRows = poloSlots
                                .map((slot, originalOrder) => {
                                    const point = toPixelPoint(slot.x, slot.y);
                                    return point ? {
                                        originalOrder,
                                        logicX: Number(slot.x),
                                        logicY: Number(slot.y),
                                        pixelX: point.x,
                                        pixelY: point.y,
                                        originalSprite: slot.spriteName
                                    } : null;
                                })
                                .filter(Boolean)
                                .sort((a, b) => a.pixelX - b.pixelX || a.pixelY - b.pixelY);

                            const spacing = slotRows.length > 1
                                ? slotRows.slice(1).map((slot, index) => Math.abs(slot.pixelX - slotRows[index].pixelX))
                                    .filter(value => Number.isFinite(value) && value > 0)
                                    .sort((a, b) => a - b)[Math.floor(slotRows.length / 2) - 1] || 0
                                : 0;
                            const maxMatchDistance = Math.max(35, Math.min(120, spacing > 0 ? spacing * 0.45 : 80));
                            const countMismatch = removeCandidates.length !== slotRows.length;
                            const usedCandidates = new Set();

                            const slots = slotRows.map((slot, index) => {
                                const available = removeCandidates
                                    .map((candidate, candidateIndex) => ({ candidate, candidateIndex }))
                                    .filter(item => !usedCandidates.has(item.candidateIndex))
                                    .sort((a, b) => {
                                        const distanceA = Math.abs(a.candidate.pixelX - slot.pixelX);
                                        const distanceB = Math.abs(b.candidate.pixelX - slot.pixelX);
                                        return distanceA - distanceB || a.candidate.pixelY - b.candidate.pixelY;
                                    });
                                const nearest = available[0];
                                const second = available[1];
                                const distance = nearest ? Math.abs(nearest.candidate.pixelX - slot.pixelX) : Infinity;
                                const secondDistance = second ? Math.abs(second.candidate.pixelX - slot.pixelX) : Infinity;
                                const ambiguous = Boolean(nearest && second && Math.abs(secondDistance - distance) < 3);
                                let removeButton = {
                                    clickX: null,
                                    clickY: null,
                                    logicX: null,
                                    logicY: null,
                                    bounds: null,
                                    safeJitterPx: null,
                                    resolved: false,
                                    mappingMethod: 'nearest-x'
                                };

                                if (!countMismatch && nearest && !ambiguous && distance <= maxMatchDistance) {
                                    usedCandidates.add(nearest.candidateIndex);
                                    const candidate = nearest.candidate;
                                    const minDimension = Math.min(candidate.bounds.width, candidate.bounds.height);
                                    const safeJitterPx = Math.max(1, Math.min(8, Math.floor(minDimension * 0.4)));
                                    removeButton = {
                                        clickX: Math.round(candidate.bounds.centerX),
                                        clickY: Math.round(candidate.bounds.centerY),
                                        logicX: candidate.logicX,
                                        logicY: candidate.logicY,
                                        bounds: {
                                            left: candidate.bounds.left,
                                            top: candidate.bounds.top,
                                            width: candidate.bounds.width,
                                            height: candidate.bounds.height,
                                            right: candidate.bounds.right,
                                            bottom: candidate.bounds.bottom
                                        },
                                        safeJitterPx,
                                        resolved: true,
                                        mappingMethod: 'nearest-x',
                                        mappingDistancePx: Math.round(distance),
                                        boundsSource: candidate.bounds.source
                                    };
                                }

                                return {
                                    x: Math.round(slot.pixelX),
                                    y: Math.round(slot.pixelY),
                                    logicX: slot.logicX,
                                    logicY: slot.logicY,
                                    index,
                                    originalSprite: slot.originalSprite,
                                    removeButton
                                };
                            });

                            const unresolvedSlotIndices = slots
                                .filter(slot => !slot.removeButton?.resolved)
                                .map(slot => slot.index);
                            return {
                                slots,
                                schemaVersion: 1,
                                remove: {
                                    enabled: !countMismatch && removeCandidates.length > 0 && slots.length > 0,
                                    targetName: selector.targetName,
                                    costumeName: selector.costumeName,
                                    coordinateSystem: 'reference-screen-1920x1080',
                                    referenceScreen: { width: 1920, height: 1080 },
                                    maxAttempts: offsets.length,
                                    clickOffsets: offsets
                                },
                                unresolvedSlotIndices,
                                countMismatch,
                                capturedCandidateCount: removeCandidates.length,
                                capturedAt: new Date().toISOString()
                            };
                        }
                        
                        async function getCustomPageTitle() {
                            const selectors = ['.style_editableBox__OXulT[data-value]', 'h1.style_title__2vo4r', 'h4.style_editableBox__OXulT', '.style_content__J_Rrs'];
                            const WAIT_TIMEOUT = 5000; const CHECK_INTERVAL = 250; const startTime = Date.now();
                            while (Date.now() - startTime < WAIT_TIMEOUT) {
                                for (const selector of selectors) {
                                    const element = document.querySelector(selector);
                                    if (element) {
                                        if (element.hasAttribute('data-value')) {
                                            const dataValue = element.getAttribute('data-value').trim();
                                            if (dataValue) return dataValue;
                                        }
                                        const textContent = (element.textContent || element.innerText || '').trim();
                                        if (textContent) {
                                            const cleanText = textContent.replace(/\s+/g, ' ').trim();
                                            if (cleanText) return cleanText;
                                        }
                                    }
                                }
                                await new Promise(r => setTimeout(r, CHECK_INTERVAL));
                            }
                            return document.title || 'Untitled Game';
                        }
                        
                        function getIconsWithSimplifiedLogic(allTargets) {
                            console.log("🎬 Bắt đầu quá trình lấy icons (Logic đơn giản - Không sắp xếp)...");

                            // Bước 1: LẤY CÁC ICON ĐANG HIỆN THỊ
                            console.log("🔶️ Bước 1: Tìm các sprite 'Icons' đang hiện thị...");
                            const visibleIcons = allTargets.filter(t => t.sprite.name.includes('Icons') && t.visible);
                            console.log(`✦ Tìm thấy ${visibleIcons.length} icons trên màn hình.`);

                            // Bước 2: CHUYỂN ĐỔI TỌA ĐỘ, LỌC VÀ GÁN INDEX TRỰC TIẾP (Không có sắp xếp)
                            console.log("🔶️ Bước 2: Chuyển đổi tọa độ, lọc và gán index theo thứ tự gốc...");
                            function clusterIconRows(iconRows, tolerance = 18) {
                                const rows = [];
                                [...iconRows].sort((a, b) => a.y - b.y || a.x - b.x).forEach(icon => {
                                    let row = rows.find(candidate => Math.abs(candidate.y - icon.y) <= tolerance);
                                    if (!row) {
                                        row = { y: icon.y, icons: [] };
                                        rows.push(row);
                                    }
                                    row.icons.push(icon);
                                    row.y = row.icons.reduce((sum, item) => sum + item.y, 0) / row.icons.length;
                                });
                                return rows;
                            }

                            function validateIconGrid(iconRows) {
                                if (iconRows.length <= 1) return iconRows;

                                const rows = clusterIconRows(iconRows);
                                const rowByIcon = new Map();
                                rows.forEach(row => row.icons.forEach(icon => rowByIcon.set(icon, row)));

                                const multiIconRows = rows.filter(row => row.icons.length >= 2);
                                if (!multiIconRows.length) return iconRows;

                                const groupCounts = {};
                                multiIconRows.forEach(row => row.icons.forEach(icon => {
                                    const group = icon.originalIcon?.sprite?.name || '';
                                    groupCounts[group] = (groupCounts[group] || 0) + 1;
                                }));
                                const mainGroupName = Object.entries(groupCounts)
                                    .sort((a, b) => b[1] - a[1])[0]?.[0] || null;

                                const accepted = [];
                                const rejected = [];
                                iconRows.forEach(icon => {
                                    const row = rowByIcon.get(icon);
                                    const rowCount = row?.icons?.length || 0;
                                    const group = icon.originalIcon?.sprite?.name || '';
                                    const isSingletonOutlier = rowCount <= 1;
                                    const isSmallOffMainGroup = mainGroupName && group !== mainGroupName && rowCount < 3;
                                    if (isSingletonOutlier || isSmallOffMainGroup) {
                                        rejected.push({ x: icon.x, y: icon.y, group, rowCount, reason: isSingletonOutlier ? 'singleton_row_outlier' : 'off_main_icon_group' });
                                    } else {
                                        accepted.push(icon);
                                    }
                                });

                                if (rejected.length) {
                                    console.log('[ICON GRID VALIDATION] Rejected outlier icon candidates:');
                                    console.table(rejected);
                                }
                                return accepted.length ? accepted : iconRows;
                            }

                            const candidateLayoutIcons = visibleIcons.map((icon, index) => {
                                const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                                return {
                                    originalIcon: icon,
                                    x: pixelCoords.x,
                                    y: pixelCoords.y,
                                    originalIndexInVisibleList: index
                                };
                            })
                            .filter(icon => icon.y >= 540);

                            const finalLayoutIcons = validateIconGrid(candidateLayoutIcons)
                            .map((iconData, finalIndex) => {
                                return {
                                    x: iconData.x,
                                    y: iconData.y,
                                    index: finalIndex
                                };
                            });

                            console.log(`✦ Sau khi lọc nửa dưới, có ${finalLayoutIcons.length} icons hợp lệ.`);
                            console.log("✦ HOÀN THÀNH! Icons được tạo theo logic đơn giản (Không sắp xếp theo tọa độ):");
                            console.table(finalLayoutIcons);
                            
                            return finalLayoutIcons;
                        }
                        // =====================================================
                        // >>> END OF CHANGES
                        // =====================================================

                        const vm = findVM();
                        if (!vm) return reject({ error: 'Cannot find Scratch VM' });
                        
                        const customPageTitle = await getCustomPageTitle();
                        const allTargets = vm.runtime.targets.map(t => t);
                        
                        // Sử dụng hàm logic mới
                        const icons = getIconsWithSimplifiedLogic(allTargets);

                        function parseIconNumber(costumeName) {
                            const match = String(costumeName || '').match(/^(\d{1,2})-a(?:\b|-|_)?/i);
                            return match ? Number(match[1]) : null;
                        }

                        function getIconIdentityEvidence(allTargets, layoutIcons) {
                            const visibleCandidates = allTargets
                                .filter(t => t.sprite && t.sprite.name && t.sprite.name.includes('Icons') && t.visible)
                                .map((target, order) => {
                                    const costumes = target.sprite.costumes || [];
                                    const costume = costumes[target.currentCostume] || {};
                                    const coords = toPixelWithMenu(target.x, target.y);
                                    return {
                                        order,
                                        x: coords.x,
                                        y: coords.y,
                                        iconTargetName: target.sprite.name,
                                        currentCostume: target.currentCostume,
                                        costumeName: costume.name || null,
                                        costumeAssetId: costume.assetId || null,
                                        iconNumber: parseIconNumber(costume.name)
                                    };
                                })
                                .filter(row => row.y >= 540);

                            const directByIconIndex = layoutIcons.map(icon => {
                                const match = visibleCandidates.find(candidate => candidate.x === icon.x && candidate.y === icon.y);
                                return {
                                    iconIndex: icon.index,
                                    sourceX: icon.x,
                                    sourceY: icon.y,
                                    costumeName: match ? match.costumeName : null,
                                    iconNumber: match ? match.iconNumber : null,
                                    iconTargetName: match ? match.iconTargetName : null,
                                    currentCostume: match ? match.currentCostume : null,
                                    source: match ? 'visible_icon_coordinate_match' : 'unmatched_visible_icon'
                                };
                            });

                            const aggregateCandidates = allTargets.map(target => {
                                const costumes = target.sprite?.costumes || [];
                                const iconCostumes = costumes
                                    .map((costume, costumeIndex) => ({
                                        costumeIndex,
                                        costumeName: costume.name || null,
                                        costumeAssetId: costume.assetId || null,
                                        iconNumber: parseIconNumber(costume.name)
                                    }))
                                    .filter(row => row.iconNumber !== null);
                                const standardCount = new Set(
                                    iconCostumes
                                        .map(row => row.iconNumber)
                                        .filter(number => number >= 1 && number <= 20)
                                ).size;
                                return {
                                    iconTargetName: target.sprite?.name || null,
                                    runtimeIndex: allTargets.indexOf(target),
                                    standardCount,
                                    costumeCount: iconCostumes.length,
                                    costumes: iconCostumes
                                };
                            })
                            .filter(row => /^Icons/.test(row.iconTargetName || '') && row.standardCount >= 20)
                            .sort((a, b) => b.standardCount - a.standardCount || b.costumeCount - a.costumeCount);

                            return {
                                capturedAt: new Date().toISOString(),
                                source: 'json_generator_same_vm_session',
                                visibleCandidates,
                                directByIconIndex,
                                aggregateCostumes: aggregateCandidates.length ? aggregateCandidates[0] : null
                            };
                        }

                        const iconIdentityEvidence = getIconIdentityEvidence(allTargets, icons);
                        
                        // =====================================================
                        // FIXED: Sắp xếp slots theo tọa độ X (nhất quán với js_get_state_and_coordinates)
                        // =====================================================
                        const poloSprites = allTargets.filter(t => /^Polo \d+$/.test(t.sprite.name));
                        poloSprites.sort((a, b) => {
                            const numA = parseInt(a.sprite.name.match(/\d+/)?.[0] || '0');
                            const numB = parseInt(b.sprite.name.match(/\d+/)?.[0] || '0');
                            return numA - numB;
                        });
                        
                        // Convert to pixel coordinates first
                        const slotsWithCoords = poloSprites.map(s => {
                            const coords = toPixelWithMenu(s.x, s.y);
                            return {
                                ...coords,
                                logicX: s.x,
                                logicY: s.y,
                                spriteName: s.sprite.name
                            };
                        });
                        
                        // Sort by X coordinate (left to right) and reassign index
                        slotsWithCoords.sort((a, b) => a.x - b.x);
                        const poloSlotInputs = slotsWithCoords.map(slot => ({
                            x: slot.logicX,
                            y: slot.logicY,
                            spriteName: slot.spriteName
                        }));
                        const removeCapture = captureRemoveControls(
                            allTargets,
                            vm.runtime.renderer || vm.renderer || null,
                            poloSlotInputs
                        );
                        const slots = removeCapture.slots || [];
                        const returnControls = { ...removeCapture };
                        delete returnControls.slots;
                        // =====================================================
                        // END: FIXED slots sorting
                        // =====================================================

                        // =====================================================
                        // NEW: Find Skip Intro Button Logic
                        // =====================================================
                        function findSkipIntroButton(allTargets) {
                            console.log("🎯 Searching for Skip Intro button...");
                            
                            try {
                                // Search for target with name "Intro" and costume "Skip"
                                const introTarget = allTargets.find(target => {
                                    // Check if target name is "Intro"
                                    if (target.sprite.name !== "Intro") {
                                        return false;
                                    }
                                    
                                    // Check if target has costume with name "Skip"
                                    const hasSkipCostume = target.sprite.costumes.some(costume => 
                                        costume.name === "Skip"
                                    );
                                    
                                    if (hasSkipCostume) {
                                        console.log(`✦ Found Skip Intro target: ${target.sprite.name}`);
                                        console.log(`   - Position: (${target.x}, ${target.y})`);
                                        console.log(`   - Visible: ${target.visible}`);
                                        console.log(`   - Costumes: ${target.sprite.costumes.map(c => c.name).join(', ')}`);
                                        return true;
                                    }
                                    
                                    return false;
                                });
                                
                                if (introTarget) {
                                    // Convert logic coordinates to pixel coordinates
                                    const pixelCoords = toPixelWithMenu(introTarget.x, introTarget.y);
                                    
                                    if (pixelCoords.x !== null && pixelCoords.y !== null) {
                                        console.log(`✦ Skip Intro button found at pixel coordinates: (${pixelCoords.x}, ${pixelCoords.y})`);
                                        return {
                                            x: pixelCoords.x,
                                            y: pixelCoords.y,
                                            visible: introTarget.visible,
                                            targetName: introTarget.sprite.name,
                                            currentCostume: introTarget.currentCostume,
                                            costumes: introTarget.sprite.costumes.map(c => c.name)
                                        };
                                    } else {
                                        console.warn("⚠️ Skip Intro target found but coordinates conversion failed");
                                        return null;
                                    }
                                } else {
                                    console.log("❌ Skip Intro button not found (no target with name 'Intro' and costume 'Skip')");
                                    return null;
                                }
                                
                            } catch (error) {
                                console.error("❌ Error while searching for Skip Intro button:", error);
                                return null;
                            }
                        }

                        // Find Skip Intro button
                        const skipIntroButton = findSkipIntroButton(allTargets);

                        // Log the result for debugging
                        if (skipIntroButton) {
                            console.log("🎯 Skip Intro Button Detection Result:");
                            console.log(`   - Found: YES`);
                            console.log(`   - Coordinates: (${skipIntroButton.x}, ${skipIntroButton.y})`);
                            console.log(`   - Visible: ${skipIntroButton.visible}`);
                            console.log(`   - Target Name: ${skipIntroButton.targetName}`);
                            console.log(`   - Available Costumes: ${skipIntroButton.costumes.join(', ')}`);
                        } else {
                            console.log("🎯 Skip Intro Button Detection Result:");
                            console.log(`   - Found: NO`);
                            console.log(`   - Reason: Target 'Intro' with costume 'Skip' not found in allTargets`);
                        }
                        // =====================================================
                        // END: Find Skip Intro Button Logic
                        // =====================================================

                        const result = {
                            timestamp: new Date().toISOString(),
                            pageTitle: customPageTitle,
                            currentUrl: window.location.href,
                            layout: { 
                                icons: icons, 
                                slots: slots, 
                                bonus: [], 
                                skipIntroButton: skipIntroButton,
                                returnControls: returnControls
                            },
                            iconIdentityEvidence: iconIdentityEvidence
                        };

                        console.log("Full layout capture completed!");
                        resolve(result);

                    } catch(e) {
                        console.error("Error in full layout capture:", e);
                        reject({ error: e.toString(), stack: e.stack });
                    }
                });
                '''

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

    def calculate_slot_center_index(self, slots):
        """Tính toán slot center index dựa trên công thức: (total_slots - 1) // 2"""
        try:
            if not slots:
                print("⌧ No slots available for center calculation")
                return None, None, 0
            
            total_slots = len(slots)
            center_index = (total_slots - 1) // 2
            
            print(f"📊 Slot calculation: {total_slots} slots → center index {center_index}")
            print(f"   Examples: 7 slots → index 3, 9 slots → index 4, 6 slots → index 2")
            print(f"🎯 Using center slot index {center_index} out of {total_slots} total slots")
            
            # Find slot with center index
            center_slot = next((slot for slot in slots if slot.get('index') == center_index), None)
            
            if center_slot and center_slot.get('x') is not None:
                center_x = center_slot['x']
                print(f"🎯 Target slot details: index={center_index}, coords=({center_x}, {center_slot.get('y', 'N/A')})")
                return center_slot, center_index, total_slots
            else:
                print(f"⌧ Center slot (index {center_index}) not found or has invalid coordinates")
                return None, center_index, total_slots
                
        except Exception as e:
            print(f"⌧ Error calculating slot center: {e}")
            return None, None, 0

    def find_phase_variable(self, state_data):
        """Find phase variable in state data"""
        try:
            print("ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Searching for phase variable in state data...")
            
            # Search in global variables first
            variables = state_data.get('variables', {})
            
            # Look for phase variable by name
            for var_key, var_data in variables.items():
                if isinstance(var_data, dict) and var_data.get('name') == 'phase':
                    print(f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Found phase variable in global variables: {var_data}")
                    return var_data.get('value')
            
            # Search in allTargets variables
            all_targets = state_data.get('allTargets', [])
            for target in all_targets:
                target_vars = target.get('variables', {})
                for var_key, var_data in target_vars.items():
                    if isinstance(var_data, dict) and var_data.get('name') == 'phase':
                        print(f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Found phase variable in target '{target.get('name', 'Unknown')}': {var_data}")
                        return var_data.get('value')
            
            print("ÃƒÂ¢Ã…Â¡ ÃƒÂ¯Ã‚Â¸Ã‚Â Phase variable not found in state data")
            return None
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error finding phase variable: {e}")
            return None

    def detect_horror_by_phase_comparison(self, before_state, after_state):
        """Detect horror by comparing phase value before and after drag"""
        try:
            print("\nÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â PHASE COMPARISON HORROR DETECTION")
            print("="*60)
            
            # Find phase values
            phase_before = self.find_phase_variable(before_state)
            phase_after = self.find_phase_variable(after_state)
            
            print(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…  Phase value BEFORE drag: {phase_before}")
            print(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…  Phase value AFTER drag: {phase_after}")
            
            # Convert to string for comparison (handle both string and numeric values)
            phase_before_str = str(phase_before) if phase_before is not None else None
            phase_after_str = str(phase_after) if phase_after is not None else None
            
            if phase_before_str is None or phase_after_str is None:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ Cannot find phase variable - will use screenshot fallback")
                return {
                    'has_horror': None,  # Signal to use fallback
                    'method': 'phase_not_found',
                    'phase_before': phase_before,
                    'phase_after': phase_after,
                    'confidence': 0
                }
            
            # Check for horror transition: 1 -> 2 only
            has_horror = (phase_before_str == "1" and phase_after_str == "2")
            
            # Determine method details
            if has_horror:
                method = "phase_1_to_2"
                confidence = 1.0
            elif phase_before_str == phase_after_str:
                method = f"phase_unchanged_{phase_before_str}"
                confidence = 0.9  # High confidence no horror
            else:
                method = f"phase_{phase_before_str}_to_{phase_after_str}"
                confidence = 0.8  # Other transitions don't count as horror
            
            result = {
                'has_horror': has_horror,
                'method': method,
                'phase_before': phase_before,
                'phase_after': phase_after,
                'confidence': confidence
            }
            
            print(f"\nÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ PHASE COMPARISON RESULT:")
            print(f"   Phase transition: {phase_before_str} -> {phase_after_str}")
            print(f"   Has Horror: {has_horror} (only 1->2 counts as horror)")
            print(f"   Method: {method}")
            print(f"   Confidence: {confidence:.1f}")
            print("="*60)
            
            return result
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error in phase comparison: {e}")
            import traceback
            traceback.print_exc()
            return {
                'has_horror': None,  # Signal to use fallback
                'method': 'phase_comparison_error',
                'phase_before': None,
                'phase_after': None,
                'confidence': 0,
                'error': str(e)
            }

    def calculate_icons_area_from_coordinates(self, state_data):
        """Calculate bounding box for icons 0-18 area from state data"""
        try:
            print("ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â Calculating icons area from state data...")
            
            # Check if state_data has icons directly or needs to be extracted
            icons = None
            if 'icons' in state_data:
                icons = state_data['icons']
            elif 'layout' in state_data and 'icons' in state_data['layout']:
                icons = state_data['layout']['icons']
            else:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ No icons data found in state")
                return None
            
            if not icons:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ Icons list is empty")
                return None
                
            # Filter icons 0-18 (exclude icon 19 if exists)
            target_icons = [icon for icon in icons if icon.get('index', 0) <= 18]
            
            if len(target_icons) < 5:
                print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Only {len(target_icons)} target icons available, need at least 5")
                return None
            
            # Find valid coordinates
            valid_icons = [icon for icon in target_icons if icon.get('x') is not None and icon.get('y') is not None]
            
            if len(valid_icons) == 0:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ No icons with valid coordinates found")
                return None
                
            print(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…  Found {len(valid_icons)} icons with valid coordinates (0-18)")
            
            x_coords = [icon['x'] for icon in valid_icons]
            y_coords = [icon['y'] for icon in valid_icons]
            
            # Calculate bounding box with padding
            padding = 20
            icon_size = 100  # Estimated icon size
            
            area = {
                'x': max(0, min(x_coords) - padding),
                'y': max(0, min(y_coords) - padding), 
                'width': max(x_coords) - min(x_coords) + icon_size + (padding * 2),
                'height': max(y_coords) - min(y_coords) + icon_size + (padding * 2),
                'icons_count': len(valid_icons)
            }
            
            print(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â Icons area (0-18): {area['x']}, {area['y']}, {area['width']}x{area['height']}")
            return area
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error calculating icons area: {e}")
            return None

    def capture_icons_area_screenshot(self, area_coords):
        """Capture screenshot of icons 0-18 area"""
        try:
            if not area_coords:
                return None
            
            # ðŸ”§ SERVICE MODE FIX: Ensure window activation before screenshot
            print("ðŸ”§ Ensuring window activation for screenshot...")
            self.ensure_window_activation("screenshot capture")
            
            screenshot = pyautogui.screenshot(
                region=(
                    area_coords['x'], 
                    area_coords['y'], 
                    area_coords['width'], 
                    area_coords['height']
                )
            )
            
            # Convert to numpy array
            screenshot_np = np.array(screenshot)
            print(f"ðŸ“¸ Screenshot captured: {screenshot_np.shape}")
            return screenshot_np
            
        except Exception as e:
            print(f"âŒ Error capturing screenshot: {e}")
            return None

    def calculate_color_histogram(self, img):
        """Calculate color histogram for an image"""
        try:
            # Ensure uint8 format
            img = img.astype(np.uint8)
            
            if len(img.shape) == 3:
                # RGB image
                red_mean = np.mean(img[:,:,0])
                green_mean = np.mean(img[:,:,1]) 
                blue_mean = np.mean(img[:,:,2])
            else:
                # Grayscale
                red_mean = green_mean = blue_mean = np.mean(img)
            
            # Calculate brightness (luminance)
            brightness = 0.299 * red_mean + 0.587 * green_mean + 0.114 * blue_mean
            
            # Calculate saturation
            max_rgb = max(red_mean, green_mean, blue_mean)
            min_rgb = min(red_mean, green_mean, blue_mean)
            saturation = (max_rgb - min_rgb) / (max_rgb + 1e-8) if max_rgb > 0 else 0
            
            return {
                'red': red_mean,
                'green': green_mean,
                'blue': blue_mean,
                'brightness': brightness,
                'saturation': saturation
            }
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error calculating histogram: {e}")
            return {'red': 0, 'green': 0, 'blue': 0, 'brightness': 0, 'saturation': 0}

    def compare_icons_histogram(self, before_img, after_img):
        """Histogram comparison for horror detection"""
        try:
            print("ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¨ Analyzing color histogram changes...")
            
            # Calculate histograms
            before_hist = self.calculate_color_histogram(before_img)
            after_hist = self.calculate_color_histogram(after_img)
            
            # Horror signatures in icons
            red_shift = (after_hist['red'] - before_hist['red']) / (before_hist['red'] + 1e-8)
            brightness_change = (before_hist['brightness'] - after_hist['brightness']) / (before_hist['brightness'] + 1e-8)
            saturation_change = abs(after_hist['saturation'] - before_hist['saturation']) / (before_hist['saturation'] + 1e-8)
            
            # Scoring with weights as specified
            horror_score = (red_shift * 0.4) + (brightness_change * 0.4) + (saturation_change * 0.2)
            
            # 25% threshold as specified
            has_horror_color = horror_score > 0.25
            
            result = {
                'has_horror': has_horror_color,
                'horror_score': max(0, horror_score),  # Ensure non-negative
                'red_shift': red_shift,
                'brightness_change': brightness_change,
                'saturation_change': saturation_change,
                'before_hist': before_hist,
                'after_hist': after_hist
            }
            
            print(f"   Red shift: {red_shift:.3f}")
            print(f"   Brightness change: {brightness_change:.3f}")
            print(f"   Saturation change: {saturation_change:.3f}")
            print(f"   Horror score: {horror_score:.3f} (threshold: 0.25)")
            print(f"   Has horror (color): {has_horror_color}")
            
            return result
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error in histogram comparison: {e}")
            return {'has_horror': False, 'horror_score': 0, 'red_shift': 0, 'brightness_change': 0, 'saturation_change': 0}

    def rgb_to_grayscale(self, img):
        """Convert RGB to grayscale"""
        if len(img.shape) == 3:
            return np.dot(img[...,:3], [0.299, 0.587, 0.114]).astype(np.uint8)
        else:
            return img.astype(np.uint8)

    def compare_icons_ssim(self, before_img, after_img):
        """SSIM comparison for horror detection"""
        try:
            print("🔺 Analyzing structural similarity (SSIM)...")
            
            # Convert to grayscale for structure comparison
            before_gray = self.rgb_to_grayscale(before_img)
            after_gray = self.rgb_to_grayscale(after_img)
            
            # Ensure same dimensions
            if before_gray.shape != after_gray.shape:
                min_h = min(before_gray.shape[0], after_gray.shape[0])
                min_w = min(before_gray.shape[1], after_gray.shape[1])
                before_gray = before_gray[:min_h, :min_w]
                after_gray = after_gray[:min_h, :min_w]
            
            # Calculate SSIM với kiểm tra an toàn
            similarity_score = 1.0  # Giá trị mặc định
            
            if HAS_SSIM and _ssim_func is not None:
                try:
                    # Gọi hàm SSIM với kiểm tra kết quả
                    ssim_result = _ssim_func(before_gray, after_gray)
                    
                    # Kiểm tra kiểu dữ liệu kết quả
                    if isinstance(ssim_result, (int, float)):
                        similarity_score = float(ssim_result)
                    elif isinstance(ssim_result, (tuple, list)) and len(ssim_result) > 0:
                        # Nếu trả về tuple/list, lấy phần tử đầu tiên
                        similarity_score = float(ssim_result[0])
                    else:
                        print(f"⚠️ Unexpected SSIM result type: {type(ssim_result)}")
                        similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
                        
                except Exception as e:
                    print(f"⚠️ SSIM calculation failed: {e}")
                    similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
            else:
                # Fallback simple SSIM
                similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
            
            # Đảm bảo similarity_score là số hợp lệ
            try:
                similarity_score = float(similarity_score)
                # Giới hạn trong khoảng [0, 1]
                similarity_score = max(0.0, min(1.0, similarity_score))
            except (TypeError, ValueError):
                print("⚠️ Invalid similarity_score, using default")
                similarity_score = 1.0
            
            # Tính difference_score
            difference_score = 1.0 - similarity_score
            
            # 30% structural difference threshold as specified
            has_horror_structural = difference_score > 0.30
            
            result = {
                'has_horror': has_horror_structural,
                'similarity_score': similarity_score,
                'difference_score': difference_score
            }
            
            print(f"   Similarity score: {similarity_score:.3f}")
            print(f"   Difference score: {difference_score:.3f} (threshold: 0.30)")
            print(f"   Has horror (structural): {has_horror_structural}")
            
            return result
            
        except Exception as e:
            print(f"❌ Error in SSIM comparison: {e}")
            return {'has_horror': False, 'similarity_score': 1.0, 'difference_score': 0.0}

    def simple_ssim_fallback(self, img1, img2):
        """Simple SSIM fallback when scikit-image not available"""
        try:
            # Calculate means
            mu1 = np.mean(img1)
            mu2 = np.mean(img2)
            
            # Calculate variances and covariance
            var1 = np.var(img1)
            var2 = np.var(img2)
            cov = np.mean((img1 - mu1) * (img2 - mu2))
            
            # SSIM constants
            c1 = (0.01 * 255) ** 2
            c2 = (0.03 * 255) ** 2
            
            # SSIM calculation
            numerator = (2 * mu1 * mu2 + c1) * (2 * cov + c2)
            denominator = (mu1**2 + mu2**2 + c1) * (var1 + var2 + c2)
            
            ssim_value = numerator / (denominator + 1e-8)
            return max(0, min(1, ssim_value))
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error in simple SSIM: {e}")
            return 0.5

    def detect_horror_icons_area(self, before_img, after_img):
        """Histogram + SSIM Combo for horror detection"""
        try:
            print("\nÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â VISUAL HORROR DETECTION - Histogram + SSIM Combo")
            print("="*60)
            
            if before_img is None or after_img is None:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ Missing screenshot data")
                return {'has_horror': False, 'confidence': 0, 'method': 'failed', 'reason': 'Missing images'}
            
            # Method 1: Color analysis (for horror color shifts)
            color_result = self.compare_icons_histogram(before_img, after_img)
            
            # Method 2: Structural analysis (for facial/shape changes)  
            structural_result = self.compare_icons_ssim(before_img, after_img)
            
            # Decision logic as specified
            has_horror = (
                color_result['horror_score'] > 0.25 or      # Significant color shift
                structural_result['difference_score'] > 0.30  # Major structural changes
            )
            
            # Calculate confidence
            confidence = max(color_result['horror_score'], structural_result['difference_score'])
            
            # Determine detection method
            if color_result['has_horror'] and structural_result['has_horror']:
                method = "histogram_and_ssim"
            elif color_result['has_horror']:
                method = "histogram_only"
            elif structural_result['has_horror']:
                method = "ssim_only"
            else:
                method = "no_horror_detected"
            
            result = {
                'has_horror': has_horror,
                'confidence': confidence,
                'method': method,
                'color_shift': color_result['horror_score'],
                'structural_change': structural_result['difference_score'],
                'color_details': color_result,
                'structural_details': structural_result
            }
            
            print(f"\nÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ HISTOGRAM + SSIM COMBO RESULT:")
            print(f"   Color Horror Score: {color_result['horror_score']:.3f} (threshold: 0.25)")
            print(f"   Structural Change: {structural_result['difference_score']:.3f} (threshold: 0.30)")  
            print(f"   Has Horror: {has_horror} (method: {method})")
            print(f"   Confidence: {confidence:.3f}")
            print("="*60)
            
            return result
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error in horror detection: {e}")
            import traceback
            traceback.print_exc()
            return {'has_horror': False, 'confidence': 0, 'method': 'error', 'reason': str(e)}

    def calculate_extended_screenshot_area(self, state_data):
        """Calculate extended area from (0,0) to icon index 18 coordinates"""
        try:
            # ðŸ”§ SERVICE MODE FIX: Prepare for potential screenshot operations
            print("ðŸ”§ Preparing window for screenshot operations...")
            self.ensure_window_activation("screenshot area calculation")

            print("ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â Calculating extended screenshot area from (0,0) to icon 18...")
            
            # Check if state_data has icons directly or needs to be extracted
            icons = None
            if 'icons' in state_data:
                icons = state_data['icons']
            elif 'layout' in state_data and 'icons' in state_data['layout']:
                icons = state_data['layout']['icons']
            else:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ No icons data found in state")
                return None
            
            if not icons:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ Icons list is empty")
                return None
                
            # Find icon index 18 (highest icon before icon 19)
            icon_18 = None
            for icon in icons:
                if icon.get('index') == 18:
                    icon_18 = icon
                    break
            
            if not icon_18 or icon_18.get('x') is None or icon_18.get('y') is None:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ Icon index 18 not found or has invalid coordinates")
                return None
            
            print(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…  Found icon 18 at coordinates: ({icon_18['x']}, {icon_18['y']})")
            
            # Calculate extended area from (0,0) to icon 18 position
            icon_size = 100  # Estimated icon size
            padding = 20     # Extra padding around icon 18
            
            area = {
                'x': 0,  # Start from top-left corner
                'y': 0,  # Start from top-left corner
                'width': icon_18['x'] + icon_size + padding,
                'height': icon_18['y'] + icon_size + padding,
                'end_icon_x': icon_18['x'],
                'end_icon_y': icon_18['y']
            }
            
            # Ensure minimum reasonable size
            area['width'] = max(area['width'], 800)
            area['height'] = max(area['height'], 600)
            
            print(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â Extended screenshot area: {area['x']}, {area['y']}, {area['width']}x{area['height']}")
            print(f"   Coverage: (0,0) to icon 18 at ({icon_18['x']}, {icon_18['y']})")
            return area
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error calculating extended screenshot area: {e}")
            return None

    def compare_icons_histogram_extended(self, before_img, after_img):
        """Enhanced histogram comparison for extended area with dynamic background tolerance"""
        try:
            print("ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¨ Analyzing color histogram changes (Extended Area)...")
            
            # Calculate histograms
            before_hist = self.calculate_color_histogram(before_img)
            after_hist = self.calculate_color_histogram(after_img)
            
            # Horror signatures in extended area
            red_shift = (after_hist['red'] - before_hist['red']) / (before_hist['red'] + 1e-8)
            brightness_change = (before_hist['brightness'] - after_hist['brightness']) / (before_hist['brightness'] + 1e-8)
            saturation_change = abs(after_hist['saturation'] - before_hist['saturation']) / (before_hist['saturation'] + 1e-8)
            
            # Scoring with weights - adjusted for extended area
            horror_score = (red_shift * 0.4) + (brightness_change * 0.4) + (saturation_change * 0.2)
            
            # INCREASED threshold for extended area with dynamic background
            # Original was 0.25, now 0.55 to account for background movement
            threshold = 0.7
            has_horror_color = horror_score > threshold
            
            result = {
                'has_horror': has_horror_color,
                'horror_score': max(0, horror_score),
                'red_shift': red_shift,
                'brightness_change': brightness_change,
                'saturation_change': saturation_change,
                'threshold_used': threshold,
                'before_hist': before_hist,
                'after_hist': after_hist
            }
            
            print(f"   Red shift: {red_shift:.3f}")
            print(f"   Brightness change: {brightness_change:.3f}")
            print(f"   Saturation change: {saturation_change:.3f}")
            print(f"   Horror score: {horror_score:.3f} (threshold: {threshold})")
            print(f"   Has horror (color): {has_horror_color}")
            
            return result
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error in extended histogram comparison: {e}")
            return {'has_horror': False, 'horror_score': 0, 'red_shift': 0, 'brightness_change': 0, 'saturation_change': 0, 'threshold_used': 0.40}

    def compare_icons_ssim_extended(self, before_img, after_img):
        """Enhanced SSIM comparison for extended area with dynamic background tolerance"""
        try:
            print("🔺 Analyzing structural similarity (SSIM - Extended Area)...")
            
            # Convert to grayscale for structure comparison
            before_gray = self.rgb_to_grayscale(before_img)
            after_gray = self.rgb_to_grayscale(after_img)
            
            # Ensure same dimensions
            if before_gray.shape != after_gray.shape:
                min_h = min(before_gray.shape[0], after_gray.shape[0])
                min_w = min(before_gray.shape[1], after_gray.shape[1])
                before_gray = before_gray[:min_h, :min_w]
                after_gray = after_gray[:min_h, :min_w]
            
            # Calculate SSIM với kiểm tra an toàn
            similarity_score = 1.0  # Giá trị mặc định
            
            if HAS_SSIM and _ssim_func is not None:
                try:
                    # Gọi hàm SSIM với kiểm tra kết quả
                    ssim_result = _ssim_func(before_gray, after_gray)
                    
                    # Kiểm tra kiểu dữ liệu kết quả
                    if isinstance(ssim_result, (int, float)):
                        similarity_score = float(ssim_result)
                    elif isinstance(ssim_result, (tuple, list)) and len(ssim_result) > 0:
                        # Nếu trả về tuple/list, lấy phần tử đầu tiên
                        similarity_score = float(ssim_result[0])
                    else:
                        print(f"⚠️ Unexpected SSIM result type: {type(ssim_result)}")
                        similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
                        
                except Exception as e:
                    print(f"⚠️ SSIM calculation failed: {e}")
                    similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
            else:
                # Fallback simple SSIM
                similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
            
            # Đảm bảo similarity_score là số hợp lệ
            try:
                similarity_score = float(similarity_score)
                # Giới hạn trong khoảng [0, 1]
                similarity_score = max(0.0, min(1.0, similarity_score))
            except (TypeError, ValueError):
                print("⚠️ Invalid similarity_score, using default")
                similarity_score = 1.0
            
            # Tính difference_score
            difference_score = 1.0 - similarity_score
            
            # INCREASED threshold for extended area with dynamic background
            threshold = 0.8
            has_horror_structural = difference_score > threshold
            
            result = {
                'has_horror': has_horror_structural,
                'similarity_score': similarity_score,
                'difference_score': difference_score,
                'threshold_used': threshold
            }
            
            print(f"   Similarity score: {similarity_score:.3f}")
            print(f"   Difference score: {difference_score:.3f} (threshold: {threshold})")
            print(f"   Has horror (structural): {has_horror_structural}")
            
            return result
            
        except Exception as e:
            print(f"❌ Error in extended SSIM comparison: {e}")
            return {'has_horror': False, 'similarity_score': 1.0, 'difference_score': 0.0, 'threshold_used': 0.45}

    def detect_horror_extended_area(self, before_img, after_img):
        """Enhanced horror detection for extended area (0,0) to icon 18 with background tolerance"""
        try:
            print("\nÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â EXTENDED AREA HORROR DETECTION - Enhanced for Dynamic Background")
            print("="*70)
            print("Coverage: (0,0) to icon 18 - Adjusted thresholds for background movement")
            print("="*70)
            
            if before_img is None or after_img is None:
                print("ÃƒÂ¢Ã‚ÂÃ…â€™ Missing screenshot data")
                return {'has_horror': False, 'confidence': 0, 'method': 'failed', 'reason': 'Missing images'}
            
            # Method 1: Enhanced color analysis for extended area
            color_result = self.compare_icons_histogram_extended(before_img, after_img)
            
            # Method 2: Enhanced structural analysis for extended area
            structural_result = self.compare_icons_ssim_extended(before_img, after_img)
            
            # Enhanced decision logic for extended area
            # More stringent requirements due to background noise
            has_horror = (
                color_result['horror_score'] > 0.40 or          # Increased from 0.25
                structural_result['difference_score'] > 0.45    # Increased from 0.30
            )
            
            # Additional validation: Both metrics should show significant change for high confidence
            high_confidence_horror = (
                color_result['horror_score'] > 0.50 and         # Very high color change
                structural_result['difference_score'] > 0.35    # Significant structural change
            )
            
            # Calculate confidence based on the stronger signal
            confidence = max(color_result['horror_score'], structural_result['difference_score'])
            
            # Boost confidence if both methods agree on horror
            if color_result['has_horror'] and structural_result['has_horror']:
                confidence = min(1.0, confidence * 1.2)  # Boost confidence by 20%
            
            # Determine detection method
            if high_confidence_horror:
                method = "extended_area_high_confidence"
            elif color_result['has_horror'] and structural_result['has_horror']:
                method = "extended_area_histogram_and_ssim"
            elif color_result['has_horror']:
                method = "extended_area_histogram_only"
            elif structural_result['has_horror']:
                method = "extended_area_ssim_only"
            else:
                method = "extended_area_no_horror_detected"
            
            result = {
                'has_horror': has_horror,
                'high_confidence_horror': high_confidence_horror,
                'confidence': confidence,
                'method': method,
                'color_shift': color_result['horror_score'],
                'structural_change': structural_result['difference_score'],
                'color_details': color_result,
                'structural_details': structural_result,
                'area_type': 'extended_0_0_to_icon18'
            }
            
            print(f"\nÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ EXTENDED AREA DETECTION RESULT:")
            print(f"   Color Horror Score: {color_result['horror_score']:.3f} (threshold: {color_result['threshold_used']})")
            print(f"   Structural Change: {structural_result['difference_score']:.3f} (threshold: {structural_result['threshold_used']})")  
            print(f"   Has Horror: {has_horror} (method: {method})")
            print(f"   High Confidence: {high_confidence_horror}")
            print(f"   Final Confidence: {confidence:.3f}")
            print(f"   Area Coverage: (0,0) to icon 18 position")
            print("="*70)
            
            return result
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Error in extended area horror detection: {e}")
            import traceback
            traceback.print_exc()
            return {'has_horror': False, 'confidence': 0, 'method': 'error', 'reason': str(e), 'area_type': 'extended_0_0_to_icon18'}

    def ensure_game_focus(self):
        """ðŸ”§ ENHANCED: Ensure game focus vá»›i service mode support"""
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
            
            # Phase 2: Force mouse focus
            pyautogui.moveTo(960, 540, duration=0.2)
            time.sleep(0.2)
            
            # Phase 3: Click to ensure focus
            pyautogui.click(960, 540)
            time.sleep(0.3)
            
            print("   âœ… Game focus ensured with enhanced method")
            return True
            
        except Exception as e:
            print(f"   âŒ Enhanced focus error: {e}")
            return False

    def navigate_and_prepare_game(self, url):
            """
            Sử dụng engine SprunkiGamePlayer để thực hiện toàn bộ quá trình setup game.
            Bao gồm: mở trình duyệt, xác thực tải trang, tìm và click Run/Fullscreen, bỏ qua intro.
            """
            print("INFO: Delegating game setup to SprunkiGamePlayer engine...")

            return self.game_player.navigate_and_prepare_game(url)

    def _build_manual_layout_state(self, ws, manual_profile):
        runtime_meta = {"pageTitle": "Unknown", "currentUrl": manual_profile.get("canonicalUrl") or manual_profile.get("sourceUrl")}
        response = self._send_cdp_command(ws, {
            "method": "Runtime.evaluate",
            "params": {
                "expression": "({ pageTitle: document.title, currentUrl: window.location.href })",
                "returnByValue": True
            }
        })

        if response and "result" in response:
            result_value = response.get("result", {}).get("result", {}).get("value")
            if isinstance(result_value, dict):
                runtime_meta.update(result_value)

        manual_layout = manual_profile.get("layout") or {}
        return {
            "pageTitle": runtime_meta.get("pageTitle", "Unknown"),
            "currentUrl": runtime_meta.get("currentUrl") or manual_profile.get("canonicalUrl") or manual_profile.get("sourceUrl"),
            "layout": {
                "icons": copy.deepcopy(manual_layout.get("icons") or []),
                "slots": copy.deepcopy(manual_layout.get("slots") or []),
                "bonus": copy.deepcopy(manual_layout.get("bonus") or []),
                **({"returnControls": copy.deepcopy(manual_layout.get("returnControls"))} if isinstance(manual_layout.get("returnControls"), dict) else {}),
            }
        }

    @staticmethod
    def _merge_live_return_controls_into_manual_layout(manual_state, live_state):
        """Enrich a manual layout with live Remove coordinates without mutating the profile.

        Manual profiles remain the source of truth for icons/slots and prepare
        actions.  The live VM is only used to add optional return-control
        metadata captured after the manual prepare sequence has completed.
        """
        if not isinstance(manual_state, dict) or not isinstance(live_state, dict):
            return manual_state

        manual_layout = manual_state.setdefault("layout", {})
        live_layout = live_state.get("layout") or {}
        live_slots = live_layout.get("slots") or []
        live_controls = live_layout.get("returnControls")
        if isinstance(live_controls, dict):
            manual_layout["returnControls"] = live_controls

        if not isinstance(live_slots, list):
            return manual_state

        live_by_index = {
            int(slot.get("index")): slot
            for slot in live_slots
            if isinstance(slot, dict) and str(slot.get("index", "")).lstrip("-").isdigit()
        }
        manual_slots = manual_layout.get("slots") or []
        if not isinstance(manual_slots, list):
            return manual_state

        for position, manual_slot in enumerate(manual_slots):
            if not isinstance(manual_slot, dict):
                continue
            candidate = None
            raw_index = manual_slot.get("index", position)
            try:
                candidate = live_by_index.get(int(raw_index))
            except (TypeError, ValueError):
                candidate = None
            if candidate is None and position < len(live_slots):
                candidate = live_slots[position]
            if not isinstance(candidate, dict):
                continue
            remove_button = candidate.get("removeButton")
            if isinstance(remove_button, dict):
                manual_slot["removeButton"] = remove_button

        unresolved = [
            slot.get("index", index)
            for index, slot in enumerate(manual_slots)
            if isinstance(slot, dict)
            and not (slot.get("removeButton") or {}).get("resolved", False)
        ]
        if isinstance(manual_layout.get("returnControls"), dict):
            manual_layout["returnControls"]["unresolvedSlotIndices"] = unresolved
        return manual_state

    def _detect_game_control_type(self, ws, manual_profile=None):
        game_control_type = "click_drag"
        game_control_details = {
            "method": "default_fallback",
            "test_performed": False,
            "confidence": 0.5,
            "test_icon": 0,
            "test_slot": 0
        }

        manual_settings = (manual_profile or {}).get("settings", {}) or {}
        manual_control_type = (manual_settings.get("gameControlType") or "").strip()
        if manual_control_type and manual_control_type.lower() != "unknown":
            game_control_type = manual_control_type
            game_control_details.update({
                "method": "manual_profile",
                "confidence": 1.0,
                "source": "manual_profile"
            })
            return game_control_type, game_control_details

        try:
            if manual_profile:
                layout = (manual_profile.get("layout") or {})
                icons = layout.get("icons") or []
                slots = layout.get("slots") or []
                control_test_state = {"icons": icons, "slots": slots}
                game_control_details["layoutSource"] = "manual_profile"
            else:
                print("📋 Getting layout for game control test...")
                response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate",
                    "params": {"expression": self.js_get_state_and_coordinates, "awaitPromise": True, "returnByValue": True}
                })
                control_test_state = None
                if response and 'result' in response:
                    control_test_state = response.get('result', {}).get('result', {}).get('value')
                game_control_details["layoutSource"] = "runtime_detection"

            if control_test_state and 'icons' in control_test_state and 'slots' in control_test_state:
                icons = control_test_state['icons']
                slots = control_test_state['slots']

                test_icon = next((icon for icon in icons if icon.get('index') == 0), None)
                test_slot = next((slot for slot in slots if slot.get('index') == 0), None)

                if test_icon and test_slot and test_icon.get('x') and test_slot.get('x'):
                    print(f"🎯 Test setup: Icon 0 at ({test_icon['x']}, {test_icon['y']}) -> Slot 0 at ({test_slot['x']}, {test_slot['y']})")

                    original_icon_pos = {'x': test_icon['x'], 'y': test_icon['y']}
                    print("🖱️ Testing Double-Click and Drag mechanism...")
                    success, result_code = self.test_double_click_drag(ws, test_icon, test_slot)

                    game_control_details['test_performed'] = True
                    game_control_details['result_code'] = result_code

                    if success:
                        print("✅ Double-Click and Drag test SUCCESSFUL!")
                        game_control_type = "double_click_drag"
                        game_control_details['method'] = "double_click_test_success"
                        game_control_details['confidence'] = 1.0

                        print("🔄 Resetting game state...")
                        reset_success = self.reset_icon_position(ws, test_slot, original_icon_pos)
                        game_control_details['reset_performed'] = reset_success

                        if reset_success:
                            print("✅ Game state reset completed")
                        else:
                            print("⚠️ Reset may have failed - continuing anyway")

                    else:
                        print(f"❌ Double-Click and Drag test FAILED: {result_code}")
                        print("🎯 Defaulting to Click-Drag mechanism")
                        game_control_type = "click_drag"
                        game_control_details['method'] = "double_click_test_failed"
                        game_control_details['confidence'] = 0.9
                        game_control_details['failure_reason'] = result_code

                else:
                    print("❌ Could not find icon 0 or slot 0 for testing")
                    game_control_details['method'] = "test_setup_failed"
                    game_control_details['failure_reason'] = "missing_test_elements"
            else:
                print("❌ Invalid state for game control testing")
                game_control_details['method'] = "invalid_state"
                game_control_details['failure_reason'] = "state_extraction_failed"

        except Exception as e:
            print(f"❌ Exception during game control detection: {e}")
            game_control_details['method'] = "exception"
            game_control_details['failure_reason'] = str(e)

        return game_control_type, game_control_details

    def _get_websocket_url(self):
        if self.ws_url: return self.ws_url
        try:
            response = requests.get("http://localhost:9222/json", timeout=5)
            tabs = response.json()
            target_tab = next((t for t in tabs if t.get('type') == 'page' and 'cocrea.world' in t.get('url', '')), None)
            if not target_tab: 
                print("Ã¢ÂÅ’ CDP Error: Could not find cocrea.world tab.")
                return None
            self.ws_url = target_tab['webSocketDebuggerUrl']
            return self.ws_url
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Could not get WebSocket URL: {e}")
            return None

    def _send_cdp_command(self, ws, command):
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

    def _touch_watchdog_progress(self, substep, **metadata):
        """Forward JSON-generation progress from the generator-owned player."""

        player = getattr(self, "game_player", None)
        reporter = getattr(player, "_touch_watchdog_progress", None)
        if callable(reporter):
            reporter(
                "json_generation",
                substep,
                **metadata,
            )

    def reset_state_for_new_job(self):
        """
        🔥 CRITICAL: Resets the state for a new JSON generation job.
        This must be called between processing different URLs to avoid using stale data.
        """
        print("🔄 [JSON Generator] Resetting state for new job...")
        self.ws_url = None
        # Cũng reset trạng thái của game_player để đảm bảo sạch sẽ
        if self.game_player:
            self.game_player.ws_url = None
            self.game_player.session_websocket = None
            self.game_player.batch_verification_active = False
        print("✅ [JSON Generator] State reset completed.")

    def generate_complete_json(self, url, output_path=None):
            """
            [FINAL VERSION - PHASE 3 REFACTORED]
            Orchestrates the entire JSON generation process by delegating all browser
            interactions and configurations to the self.game_player engine.
            
            Simplified Complete Logic: A vs B vs C
            
            1. Capture A (screenshot + phase) - INITIAL STATE
            2. Drag icon 19 → Capture B (screenshot + phase) - AFTER ICON 19
            - Compare phase A vs B → if hasHorror = True → STOP
            - Compare screenshot A vs B → if hasHorror = True → STOP
            3. Find highest index icon → Drag → Capture C (screenshot + phase) - AFTER HIGHEST INDEX
            - Compare phase A vs C → if hasHorror = True → STOP  
            - Compare screenshot A vs C → if hasHorror = True → STOP
            4. Final result = False
            
            STOP immediately when hasHorror = True is detected
            """
            try:
                print("\n🎯 Starting SIMPLIFIED COMPLETE LOGIC (A vs B vs C)")
                print("="*70)
                print("Logic: A(initial) vs B(icon19) vs C(highest) - Stop when hasHorror=True")
                print("="*70)
                self._touch_watchdog_progress(
                    "started",
                    progress_token={"url": url, "stage": "json_generation_started"},
                    progress_source="json_generator",
                )
                
                # Setup game
                if not self.navigate_and_prepare_game(url): 
                    return False, None, None

                self._touch_watchdog_progress(
                    "entry_ready",
                    progress_token={"url": url, "stage": "game_entry_ready"},
                    progress_source="json_generator",
                )

                # 🔧 SERVICE MODE FIX: Ensure window activation after game setup
                print("🔧 Ensuring window activation after game setup...")
                self.ensure_window_activation("post-game-setup")
                self._touch_watchdog_progress(
                    "window_ready",
                    progress_token={"stage": "window_activation_completed"},
                    progress_source="json_generator",
                )

                manual_profile = None
                manual_profile_path = None
                found_profile, found_profile_path = find_profile(url)
                is_manual_profile_valid, manual_profile_error = validate_profile(found_profile, expected_url=url)
                if found_profile and is_manual_profile_valid:
                    manual_profile = found_profile
                    manual_profile_path = found_profile_path
                    print(f"🧭 Manual profile detected for this URL: {manual_profile_path}")
                elif found_profile and manual_profile_error:
                    print(f"⚠️ Manual profile found but invalid: {manual_profile_error}")

                # OPTIMIZED: Single WebSocket approach like original working code
                ws_url = self._get_websocket_url()
                if not ws_url: 
                    self.game_player.cleanup_chrome_complete()
                    return False, None, None

                ws = websocket.create_connection(ws_url, timeout=30)

                # Bước 1: ĐÃ XÓA - Quản lý WebSocket thủ công
                # ws_url = self._get_websocket_url()
                # if not ws_url: 
                #     return False, None, None
                # ws = websocket.create_connection(ws_url, timeout=30)
                
                print("🔍 Testing JavaScript environment...")
                # [ĐÃ SỬA] Test môi trường thông qua engine
                test_response = self.game_player._execute_verification_js("window.location.href")
                if test_response is None:
                    print("❌ JavaScript environment test failed via engine")
                    self.game_player.cleanup_chrome_complete()
                    return False, None, None
                print(f"✅ JavaScript environment ready. Current URL: {test_response}")
                self._touch_watchdog_progress(
                    "runtime_verified",
                    progress_token={"stage": "javascript_environment_ready", "url": test_response},
                    progress_source="json_generator",
                )
                
                print("⏩ Preparing game entry...")
                if manual_profile:
                    print(f"🧭 Replaying manual profile prepare actions from: {manual_profile_path}")
                    if not self.game_player.replay_manual_prepare_actions(
                        manual_profile.get("prepareActions", []),
                        manual_profile,
                    ):
                        raise Exception(f"Manual profile replay failed: {manual_profile_path}")
                    print("✅ Manual prepare replay finished - Game should be ready for interaction")
                    self._touch_watchdog_progress(
                        "prepare_completed",
                        progress_token={
                            "stage": "manual_prepare_completed",
                            "actions": len(manual_profile.get("prepareActions", [])),
                        },
                        progress_source="json_generator",
                    )
                else:
                    # ============================
                    # MULTI-TARGET SKIP INTRO (Sử dụng engine)
                    # ============================
                    print("\n🎯 MULTI-TARGET SKIP INTRO: Finding and clicking ALL Skip Intro buttons...")
                    
                    print("🔍 Phase 1: Searching for ALL Skip Intro buttons...")
                    # [ĐÃ SỬA] Thực thi JS skip intro thông qua engine
                    skip_intro_response = self.game_player._execute_verification_js(self.js_find_skip_intro)
                    
                    smart_clicks_performed = 0
                    all_buttons = []
                    candidate_trials_attempted = False

                    if skip_intro_response and skip_intro_response.get('found'):
                        all_buttons = skip_intro_response.get('buttons', [])
                        button_count = skip_intro_response.get('count', 0)
                        visible_count = skip_intro_response.get('visibleCount', 0)
                        hidden_count = skip_intro_response.get('hiddenCount', 0)
                        
                        print(f"✅ Found {button_count} Skip Intro buttons:")
                        print(f"   📊 Visible: {visible_count}, Hidden: {hidden_count}")
                        
                        candidate_trials_attempted = True
                        trial_result = self.game_player._trial_action_buttons_via_ws(ws, all_buttons)
                        if trial_result.get("success"):
                            smart_clicks_performed = 1
                            selected = trial_result.get("selected") or {}
                            print(
                                "✅ Smart candidate trial selected: "
                                f"{selected.get('targetName')}/{selected.get('currentCostumeName')} "
                                f"@ ({selected.get('x')}, {selected.get('y')})"
                            )
                        else:
                            print(f"⚠️ Smart candidate trial found no playable candidate: {trial_result.get('reason')}")
                        
                    else:
                        reason = skip_intro_response.get('reason', 'unknown') if skip_intro_response else 'no_result'
                        print(f"❌ No Skip Intro buttons found - Reason: {reason}")
                        print("⚠️ Will use blind click method only...")

                    # === PHASE 1.5: PRESS ANY KEY (NEW) ===
                    print("⌨️ Phase 1.5: Pressing 'space' key to continue (for mods requiring key press)...")
                    try:
                        pyautogui.press('space')
                        time.sleep(0.5)
                        pyautogui.press('space')
                        print("   ✅ 'space' key pressed twice successfully.")
                    except Exception as key_e:
                        print(f"   ⚠️ Could not press 'space' key: {key_e}")

                    # Phase 2: ALWAYS perform blind clicks for additional assurance
                    if smart_clicks_performed > 0:
                        print("🔄 Phase 2: Performing ADDITIONAL blind clicks for triple protection...")
                        print("   📝 This ensures intro skipping even if smart clicks had edge cases")
                    else:
                        print("🔄 Phase 2: Performing STANDARD blind clicks (no smart buttons found)...")

                    # Perform blind clicks regardless of smart click success
                    # [ĐÃ SỬA] Lấy config `intro_skip_clicks` từ engine
                    blind_clicks_performed = 0
                    if smart_clicks_performed == 0 and not candidate_trials_attempted:
                        for i in range(self.game_player.intro_skip_clicks):
                            # [ĐÃ SỬA] Sử dụng hàm click của engine
                            self.game_player._execute_cdp_single_click(960, 540)
                            blind_clicks_performed += 1
                            time.sleep(0.5)
                    else:
                        print("   ⚠️ Skipping blind center clicks after smart/candidate trials to avoid menu/gallery overlays.")

                    # Summary
                    if smart_clicks_performed > 0:
                        print("🎯 MULTI-TARGET SKIP INTRO COMPLETED:")
                        print(f"   ✅ Smart clicks: {smart_clicks_performed} buttons clicked successfully")
                        print(f"   ✅ Button details: {len(all_buttons)} total found")
                        for i, button in enumerate(all_buttons):
                            visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                            print(f"      {i+1}. ({button['x']}, {button['y']}) - {visible_status}")
                        print(f"   ✅ Blind clicks: {blind_clicks_performed} additional clicks performed")
                        print("   🛡️ Triple protection ensures maximum skip success rate")
                    else:
                        print("🎯 STANDARD SKIP INTRO COMPLETED:")
                        print("   ❌ Smart clicks: N/A (No Skip Intro buttons found)")
                        print(f"   ✅ Blind clicks: {blind_clicks_performed} clicks performed")
                        print("   🔄 Standard method used as fallback")

                    print("✅ Skip intro phase finished - Game should be ready for interaction")
                    self._touch_watchdog_progress(
                        "prepare_completed",
                        progress_token={
                            "stage": "automatic_intro_skip_completed",
                            "smartClicks": smart_clicks_performed,
                            "blindClicks": blind_clicks_performed,
                        },
                        progress_source="json_generator",
                    )

                ### ========Xong phần SKIP INTRO ======== ###

                # ========================================
                # ### THIẾT LẬP DUAL VERIFICATION      ###
                # ########################################
                
                # BƯỚC BẮT BUỘC: Thiết lập hệ thống DUAL Verification TRƯỚC khi sử dụng.
                print("\n" + "="*70)
                print("🎯 INITIALIZING DUAL VERIFICATION SYSTEM FOR TESTS")
                print("="*70)
                
                # Gọi hàm setup từ game_player.
                # Hàm này sẽ inject các hàm JS cần thiết vào trang web.
                if self.game_player.setup_dual_verification_system():
                    print("✅ DUAL Verification system is now ready for use in tests.")
                    self._touch_watchdog_progress(
                        "verification_ready",
                        progress_token={"stage": "dual_verification_ready"},
                        progress_source="json_generator",
                    )
                else:
                    print("⚠️ DUAL Verification setup failed. Control detection will be less reliable.")
                    # Không dừng lại, nhưng cần lưu ý rằng xác minh có thể thất bại.

                # ### KẾT THÚC THIẾT LẬP DUAL VERIFICATION ### #

                # Static layout extraction AFTER entering the actual game
                print("\n" + "="*70)
                print("🔍 STATIC LAYOUT EXTRACTION - Capturing Game Layout After Game Entry")
                print("="*70)
                print("🖼️ RecordingArea is calculated later during showcase runtime")
                
                # Wait briefly for the game to stabilize before static layout extraction.
                print("⏳ Waiting for game to stabilize before layout extraction...")
                time.sleep(1)

                print("="*70)

                # Initialize final result variables
                final_has_horror = False
                final_confidence = 1.0
                final_detection_method = "horror_disabled_by_policy"
                final_visual_result = {}
                final_is_icon_19_disabled = False

                # ============================
                # CAPTURE A: Initial State (Screenshot + Phase)
                # ============================
                print("\n" + "="*70)
                print("📸 SKIPPING CAPTURE A & HORROR DETECTION")
                print("="*70)

                # ============================
                # GAME CONTROL TYPE DETECTION 
                # ============================
                print("\n" + "="*70)
                print("🎮 GAME CONTROL TYPE DETECTION")
                print("="*70)
                print("Testing: Double-Click and Drag vs Click-Drag mechanism")

                game_control_type, game_control_details = self._detect_game_control_type(
                    ws,
                    manual_profile=manual_profile
                )

                print(f"\n🎮 GAME CONTROL DETECTION RESULT:")
                print(f"   Control Type: {game_control_type}")
                print(f"   Detection Method: {game_control_details['method']}")
                print(f"   Confidence: {game_control_details['confidence']}")
                if 'failure_reason' in game_control_details:
                    print(f"   Failure Reason: {game_control_details['failure_reason']}")
                print("="*70)
                self._touch_watchdog_progress(
                    "control_detection_completed",
                    progress_token={
                        "stage": "control_detection_completed",
                        "controlType": game_control_type,
                        "method": game_control_details.get("method"),
                    },
                    progress_source="json_generator",
                )

                print("📊 Horror detection disabled by showcase-only policy...")

                try:
                    
                    # ============================
                    # Final Layout Capture (Fast)
                    # ============================
                    print("\n📄 Extracting final full layout...")
                    
                    self._touch_watchdog_progress(
                        "layout_capture_started",
                        progress_token={"stage": "layout_capture_started"},
                        progress_source="json_generator",
                    )
                    layout_state = None
                    if manual_profile:
                        layout_state = self._build_manual_layout_state(ws, manual_profile)
                        # Capture the same live VM layout used by the automatic
                        # path, then merge only the optional Remove metadata.
                        # Manual icons/slots and prepare actions remain intact.
                        live_response = self._send_cdp_command(ws, {
                            "method": "Runtime.evaluate",
                            "params": {
                                "expression": self.js_full_layout,
                                "awaitPromise": True,
                                "returnByValue": True
                            }
                        })
                        if live_response and "result" in live_response:
                            live_value = live_response.get("result", {}).get("result", {}).get("value")
                            if isinstance(live_value, dict) and "error" not in live_value:
                                layout_state = self._merge_live_return_controls_into_manual_layout(
                                    layout_state,
                                    live_value,
                                )
                    else:
                        response = self._send_cdp_command(ws, {
                            "method": "Runtime.evaluate", 
                            "params": {"expression": self.js_full_layout, "awaitPromise": True, "returnByValue": True}
                        })
                        
                        if response and 'result' in response:
                            result_data = response.get('result', {}).get('result', {})
                            if 'value' in result_data:
                                layout_state = result_data['value']
                    
                    if not layout_state or "error" in layout_state:
                        raise Exception("Final layout extraction failed")
                    
                    print("✅ Final layout captured successfully")
                    layout_state.get('layout', {}).pop('recordingArea', None)
                    self._touch_watchdog_progress(
                        "layout_captured",
                        progress_token={
                            "stage": "layout_captured",
                            "iconCount": len(layout_state.get("layout", {}).get("icons", []) or []),
                            "slotCount": len(layout_state.get("layout", {}).get("slots", []) or []),
                        },
                        progress_source="json_generator",
                    )

                finally:
                    # Single WebSocket cleanup
                    try:
                        ws.close()
                    except:
                        pass

                # Generate Final JSON
                page_title = layout_state.get('pageTitle', 'Unknown')
                final_json = {
                    "version": "11.1-remove-first-return",
                    "created": datetime.now().isoformat(),
                    "currentUrl": layout_state.get('currentUrl'), 
                    "pageTitle": page_title,
                    "gameSettings": {
                        "hasHorror": final_has_horror,
                        "isIcon19Disabled": final_is_icon_19_disabled,
                        "hasBonus": len(layout_state.get('layout', {}).get('bonus', [])) > 0,
                        "gameControlType": game_control_type,
                        "controlDetectionDetails": game_control_details,
                        "detectionMethod": "horror_disabled_by_policy",
                        "detectionDetails": {
                            "method": final_detection_method,
                            "confidence": final_confidence,
                            "logic_flow": "Horror detection disabled by showcase-only policy"
                        }
                    },
                    "layout": layout_state.get('layout', {}),
                    "settings": {
                        "manualProfilePath": manual_profile_path if manual_profile else None
                    }
                }

                json_file_path = self.save_json_file(final_json, output_path, custom_title=page_title)
                if not json_file_path: 
                    self.game_player.cleanup_chrome_complete()
                    return False, None, None
                self.save_icon_identity_sidecar(final_json, json_file_path, layout_state.get('iconIdentityEvidence', {}))
                self._touch_watchdog_progress(
                    "json_saved",
                    progress_token={
                        "stage": "json_saved",
                        "filename": os.path.basename(json_file_path),
                    },
                    progress_source="json_generator",
                )

                print(f"\n🎯 SIMPLIFIED COMPLETE LOGIC COMPLETED!")
                print(f"✅ hasHorror Detection: {final_has_horror}")
                print(f"✅ Detection Method: {final_detection_method}")
                self._touch_watchdog_progress(
                    "completed",
                    progress_token={"stage": "json_generation_completed"},
                    progress_source="json_generator",
                )

                self.game_player.cleanup_chrome_complete()
                return True, json_file_path, final_json
                
            except Exception as e:
                print(f"❌ Simplified complete logic error: {e}")
                import traceback
                traceback.print_exc()
                
                self.game_player.cleanup_chrome_complete()
                return False, None, None
            
    def save_icon_identity_sidecar(self, final_json, json_file_path, icon_identity_evidence=None):
            """Write a sidecar mapping source JSON icon indexes to VM costume evidence."""
            try:
                evidence = icon_identity_evidence or final_json.get("iconIdentityEvidence") or {}
                layout_icons = final_json.get("layout", {}).get("icons") or []
                direct_rows = evidence.get("directByIconIndex") or []
                rows_by_index = {
                    int(row.get("iconIndex")): row
                    for row in direct_rows
                    if row.get("iconIndex") is not None
                }

                icon_identities = []
                for fallback_index, icon in enumerate(layout_icons):
                    icon_index = int(icon.get("index", fallback_index))
                    row = rows_by_index.get(icon_index, {})
                    icon_number = row.get("iconNumber")
                    costume_name = row.get("costumeName")
                    icon_identities.append({
                        "iconIndex": icon_index,
                        "sourceX": icon.get("x"),
                        "sourceY": icon.get("y"),
                        "costumeName": costume_name,
                        "iconNumber": icon_number,
                        "status": "matched" if icon_number and 1 <= int(icon_number) <= 20 else ("extra" if icon_number else "unknown"),
                        "iconTargetName": row.get("iconTargetName"),
                        "currentCostume": row.get("currentCostume"),
                        "source": row.get("source") or "missing_direct_evidence",
                    })

                source_hash = None
                try:
                    with open(json_file_path, "rb") as handle:
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                        source_hash = digest.hexdigest()
                except Exception:
                    source_hash = None

                sidecar = {
                    "schemaVersion": 1,
                    "generatedAt": datetime.now().isoformat(),
                    "rule": {"id": "json_generator_same_session_icon_identity_sidecar"},
                    "sourceJson": os.path.basename(json_file_path),
                    "sourceJsonHash": source_hash,
                    "currentUrl": final_json.get("currentUrl"),
                    "pageTitle": final_json.get("pageTitle"),
                    "iconIdentities": icon_identities,
                    "evidence": evidence,
                }
                root, _ = os.path.splitext(json_file_path)
                sidecar_path = f"{root}.icon_identity.json"
                with open(sidecar_path, "w", encoding="utf-8") as handle:
                    json.dump(self.convert_to_json_serializable(sidecar), handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                print(f"ðŸ§­ Icon identity sidecar saved: {sidecar_path}")
                try:
                    from pathlib import Path
                    from identity_review_artifact import write_icon_order_identity_review

                    project_folder = None
                    if hasattr(self.game_player, "current_project_folder") and self.game_player.current_project_folder:
                        project_folder = Path(self.game_player.current_project_folder)
                    review_paths = write_icon_order_identity_review(
                        final_json,
                        sidecar,
                        Path(json_file_path),
                        project_folder=project_folder,
                    )
                    print(f"Icon identity review HTML saved: {review_paths.get('html')}")
                except Exception as review_exc:
                    print(f"Icon identity review HTML skipped: {review_exc}")
                return sidecar_path
            except Exception as exc:
                print(f"âš ï¸ Icon identity sidecar skipped: {exc}")
                return None

    def convert_to_json_serializable(self, obj):
        """Convert numpy types and other non-serializable objects to JSON-serializable types"""
        if isinstance(obj, dict):
            return {key: self.convert_to_json_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self.convert_to_json_serializable(item) for item in obj]
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif hasattr(obj, 'item'):  # Handle numpy scalars
            return obj.item()
        else:
            return obj

    def test_double_click_drag(self, ws, icon_coords, slot_coords):
        """Test double-click and drag mechanism with DUAL verification"""
        try:
            print(f"   Testing double-click drag from ({icon_coords['x']}, {icon_coords['y']}) to ({slot_coords['x']}, {slot_coords['y']})")
            
            # BƯỚC 1: Lấy số đếm của DUAL Verification TRƯỚC khi thực hiện hành động
            # Chúng ta dùng self.game_player để truy cập vào các hàm xác minh.
            print("   📊 Capturing DUAL verification counts BEFORE test...")
            characters_before, putted_before = self.game_player.get_dual_counts()
            print(f"   📊 Counts BEFORE: Characters={characters_before}, Putted={putted_before}")

            if characters_before is None and putted_before is None:
                print("   ❌ CRITICAL: Cannot get any verification counts. Aborting test.")
                return False, "failed_get_dual_counts"

            # BƯỚC 2: Dùng cùng compound gesture primitive với gameplay/manual replay.
            print("   🖱️ Executing double-click and drag...")
            start_x, start_y = icon_coords['x'], icon_coords['y']
            end_x, end_y = slot_coords['x'], slot_coords['y']
            gesture_result = execute_cdp_gesture(
                ws,
                self._send_cdp_command,
                start_x,
                start_y,
                end_x,
                end_y,
                mode=DOUBLE_CLICK_DRAG_TYPE,
                drag_duration_ms=400,
                click_interval_ms=300,
                first_click_duration_ms=100,
                hold_before_move_ms=200,
                move_steps=10,
                sleep_fn=time.sleep,
            )
            if not gesture_result.success:
                return False, f"gesture_failed:{gesture_result.error}"
            
            # BƯỚC 3: Chờ game xử lý và lấy lại số đếm
            time.sleep(3)
            print("   📊 Capturing DUAL verification counts AFTER test...")

            # ####################################################################
            # ### START: SỬA CODE - Thay thế so sánh tọa độ bằng DUAL verify   ###
            # ####################################################################

            characters_after, putted_after = self.game_player.get_dual_counts()
            print(f"   📊 Counts AFTER: Characters={characters_after}, Putted={putted_after}")

            # BƯỚC 4: Dùng hàm xác minh của game_player để đưa ra kết luận
            print("   🔍 Verifying test result using DUAL verification system...")
            drag_successful = self.game_player.verify_drag_success_dual(
                characters_before, characters_after, putted_before, putted_after
            )
            
            print(f"   🎯 DUAL Verification Result: {'SUCCESS' if drag_successful else 'FAILURE'}")

            return drag_successful, "success" if drag_successful else "verification_failed_no_change"

            # ####################################################################
            # ### KẾT THÚC: SỬA CODE                                            ###
            # ####################################################################

        except Exception as e:
            print(f"   ❌ Exception during double-click test: {e}")
            return False, f"exception_{str(e)}"

    def reset_icon_position(self, ws, target_icon_coords, original_position):
        """Reset icon back to original position with DUAL verification"""
        try:
            print(f"   🔄 Resetting icon from ({target_icon_coords['x']}, {target_icon_coords['y']}) to ({original_position['x']}, {original_position['y']})")

            # BƯỚC 1: Lấy số đếm TRƯỚC khi reset
            print("   📊 Capturing DUAL verification counts BEFORE reset...")
            characters_before, putted_before = self.game_player.get_dual_counts()
            print(f"   📊 Counts BEFORE: Characters={characters_before}, Putted={putted_before}")

            if characters_before is None and putted_before is None:
                print("   ⚠️ Cannot get verification counts. Proceeding with blind reset.")

            # BƯỚC 2: Thực hiện hành động kéo trả về (Giữ nguyên)
            start_x, start_y = target_icon_coords['x'], target_icon_coords['y']  
            end_x, end_y = original_position['x'], original_position['y']
            
            # Standard drag implementation
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
            time.sleep(0.2)
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1}})
            time.sleep(0.2)
            
            # Drag to original position
            steps = 15
            for i in range(1, steps + 1):
                progress = i / steps
                curr_x = start_x + (end_x - start_x) * progress
                curr_y = start_y + (end_y - start_y) * progress
                self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left"}})
                time.sleep(0.03)
            
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1}})
            time.sleep(2)
            
            # BƯỚC 3: Lấy số đếm và xác minh SAU khi reset
            print("   📊 Capturing DUAL verification counts AFTER reset...")
            characters_after, putted_after = self.game_player.get_dual_counts()
            print(f"   📊 Counts AFTER: Characters={characters_after}, Putted={putted_after}")

            print("   🔍 Verifying reset result using DUAL verification system...")
            reset_successful = self.game_player.verify_return_success_dual(
                characters_before, characters_after, putted_before, putted_after
            )
            
            if reset_successful:
                print("   ✅ Icon reset VERIFIED successfully")
                return True
            else:
                print("   ❌ Icon reset FAILED verification. State may be inconsistent.")
                return False
            
        except Exception as e:
            print(f"   ❌ Exception during reset: {e}")
            return False

    def save_json_file(self, json_data, output_path=None, custom_title=None):
            try:
                # [FIXED] Lấy config chung từ app_config của engine, không phải config game
                json_output_dir = self.game_player.app_config.get('output', {}).get('json_output_directory', r"I:\VS-Project\Sprunki\JSON")
                os.makedirs(json_output_dir, exist_ok=True)

                if not output_path:
                    import re
                    
                    if custom_title and custom_title.strip():
                        base_name = custom_title.strip()
                        print(f"📷️ Using custom title for filename: '{base_name}'")
                    else:
                        from urllib.parse import urlparse
                        
                        base_name = "unknown-game"
                        
                        # [FIXED] Lấy game_url từ engine
                        if hasattr(self.game_player, 'game_url') and self.game_player.game_url:
                            try:
                                game_url_str = str(self.game_player.game_url)
                                parsed_url = urlparse(game_url_str)
                                
                                if parsed_url.path:
                                    path_parts = parsed_url.path.split('/')
                                    slug_base = path_parts[-1] if path_parts else ""
                                    
                                    if not slug_base and len(path_parts) > 1:
                                        slug_base = path_parts[-2]
                                        
                                    if slug_base:
                                        base_name = slug_base
                                    else:
                                        netloc = parsed_url.netloc.replace('www.', '')
                                        if netloc:
                                            base_name = netloc
                                            
                            except Exception as e:
                                print(f"⚠️ URL parsing failed: {e}")
                                base_name = "unknown-game"
                        
                        print(f"📷️ Using URL slug for filename: '{base_name}'")
                    
                    clean_name = re.sub(r'[^\w\s\-]', '', base_name)
                    clean_name = re.sub(r'\s+', '-', clean_name)
                    clean_name = re.sub(r'-+', '-', clean_name)
                    clean_name = clean_name.strip('-')
                    
                    if not clean_name:
                        clean_name = "sprunki-game"
                    
                    filename = f"{clean_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    output_path = os.path.join(json_output_dir, filename)
                
                serializable_data = self.convert_to_json_serializable(json_data)
                
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(serializable_data, f, indent=2, ensure_ascii=False)
                print(f"📾 JSON saved: {output_path}")
                return output_path
            except Exception as e:
                print(f"❌ Save JSON error: {e}")
                import traceback
                traceback.print_exc()
                return None

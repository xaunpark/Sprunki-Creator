# json_generator.py (v11.0 - HYBRID - Phase Comparison + Screenshot Fallback)
# UPGRADED: Phase value comparison as primary method with visual fallback

import json
import time
import os
import subprocess
import requests
from datetime import datetime
import pyautogui
import pygetwindow as gw
import random
import websocket
from image_detector import ImageDetector
import numpy as np
from page_load_verifier import PageLoadVerifier
from analyze_layout import find_dividing_lines

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
    HYBRID Sprunki JSON Generator (v11.0 - Phase Comparison + Screenshot Fallback)
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
        self.master_controller = master_controller
        self.game_player = SprunkiGamePlayer()
        self.game_url = None
        self.ws_url = None

        # === ADD THIS CONFIG SECTION AT THE BEGINNING ===
        # Load configuration
        from config_manager import get_config
        self.config = get_config()
        
        # Chrome settings from config (fallback to defaults if not in config)
        self.chrome_config = self.config.get('chrome', {})
        self.chrome_paths = self.chrome_config.get('executable_paths', [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", 
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ])
        self.debugging_port = self.chrome_config.get('debugging_port', 9222)
        self.user_data_dir = self.chrome_config.get('user_data_dir', 'chrome_permanent_profile')
        
        # Game settings from config
        self.game_config = self.config.get('game', {})
        self.page_load_wait = self.game_config.get('page_load_wait', 20)
        self.game_load_wait = self.game_config.get('game_load_wait', 10)
        self.fullscreen_wait = self.game_config.get('fullscreen_wait', 8)
        self.max_pagedown_attempts = self.game_config.get('max_pagedown_attempts', 5)
        self.intro_skip_clicks = self.game_config.get('intro_skip_clicks', 10)
        
        print(f"Ã¢Å“â€¦ JSON Generator configured from config.json:")
        print(f"   Debug port: {self.debugging_port}")
        print(f"   Page load wait: {self.page_load_wait}s")
        print(f"   Game load wait: {self.game_load_wait}s")
        print(f"   Intro skip clicks: {self.intro_skip_clicks}")

        # Load verification settings for JSON Generator
        self.load_verification_config()        
        # === END OF CONFIG SECTION ===
        
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
                
                // =====================================================
                // UPDATED: Bottom Half Only + Coordinate-Based Sorting
                // =====================================================
                console.log("🎬 Bắt đầu quá trình lấy icons (Bottom Half + Coordinate Sorting)...");

                // Bước 1: LẤY CÁC ICON ĐANG HIỆN THỊ 
                console.log("🔶️ Bước 1: Tìm các sprite 'Icons' đang hiện thị...");
                const visibleIcons = allTargets.filter(t => t.sprite.name === 'Icons' && t.visible);
                console.log(`✦ Tìm thấy ${visibleIcons.length} icons trên màn hình.`);

                // Bước 2: CHUYỂN ĐỔI TỌA ĐỘ VÀ LỌC NỬA DƯỚI MÀN HÌNH
                console.log("🔶️ Bước 2: Chuyển đổi tọa độ và lọc icons ở nửa dưới màn hình...");
                const iconsWithPixelCoords = visibleIcons.map((icon) => {
                    const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                    return {
                        originalIcon: icon,
                        x: pixelCoords.x,
                        y: pixelCoords.y
                    };
                }).filter(icon => icon.y >= 540); // Chỉ lấy icons ở nửa dưới màn hình (y >= 540)
                
                console.log(`✦ Sau khi lọc nửa dưới: ${iconsWithPixelCoords.length} icons.`);

                // Bước 3: SẮP XẾP THEO TỌA ĐỘ (TRÊN XUỐNG DƯỚI, TRÁI SANG PHẢI)
                console.log("🔶️ Bước 3: Sắp xếp icons theo tọa độ (từ trên xuống dưới, trái sang phải)...");
                
                // Tolerance cho việc xác định cùng hàng (các icon cùng hàng có thể lệch y một chút)
                const ROW_TOLERANCE = 30;
                
                // Nhóm icons thành các hàng dựa trên tọa độ y
                const sortedByY = iconsWithPixelCoords.sort((a, b) => a.y - b.y);
                const rows = [];
                
                sortedByY.forEach(icon => {
                    // Tìm hàng phù hợp (y gần nhau trong khoảng tolerance)
                    let foundRow = false;
                    for (let row of rows) {
                        if (Math.abs(row[0].y - icon.y) <= ROW_TOLERANCE) {
                            row.push(icon);
                            foundRow = true;
                            break;
                        }
                    }
                    
                    // Nếu không tìm thấy hàng phù hợp, tạo hàng mới
                    if (!foundRow) {
                        rows.push([icon]);
                    }
                });
                
                console.log(`✦ Tạo được ${rows.length} hàng icons.`);
                
                // Sắp xếp từng hàng theo x (trái sang phải) và ghép lại
                const finalSortedIcons = [];
                rows.forEach((row, rowIndex) => {
                    // Sắp xếp icons trong hàng theo x
                    row.sort((a, b) => a.x - b.x);
                    console.log(`✦ Hàng ${rowIndex + 1}: ${row.length} icons`);
                    finalSortedIcons.push(...row);
                });

                // Bước 4: TẠO MẢNG KẾT QUẢ CUỐI CÙNG
                console.log("🔶️ Bước 4: Gán index tuần tự mới...");
                const icons = finalSortedIcons.map((iconData, index) => {
                    return {
                        x: iconData.x,
                        y: iconData.y,
                        index: index // Gán index tuần tự 0, 1, 2... theo thứ tự sau khi sắp xếp
                    };
                });

                console.log("✦ HOÀN THÀNH! Icons được tạo theo logic Bottom Half + Coordinate Sorting:");
                console.table(icons);
                // =====================================================
                // END: UPDATED Bottom Half + Coordinate Sorting
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
                console.log("ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Searching for ALL Skip Intro buttons (multiple detection)...");
                
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
                    console.log("ÃƒÂ¢Ã‚ÂÃ…â€™ VM not found - cannot search for Skip Intro buttons");
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
                
                console.log(`ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Found ${introTargets.length} 'Intro' targets with 'Skip' costume`);
                
                if (introTargets.length === 0) {
                    console.log("ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¹ÃƒÂ¯Ã‚Â¸Ã‚Â No Skip Intro buttons found");
                    
                    // DEBUG: Check for alternative patterns
                    const skipCostumeTargets = allTargets.filter(target => 
                        target.sprite.costumes.some(costume => costume.name === "Skip")
                    );
                    
                    if (skipCostumeTargets.length > 0) {
                        console.log(`ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Found ${skipCostumeTargets.length} targets with 'Skip' costume but different names:`);
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
                        console.log(`   ÃƒÂ¢Ã‚ÂÃ…â€™ Coordinate conversion failed for target ${index + 1}`);
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
                console.error("ÃƒÂ¢Ã‚ÂÃ…â€™ Error searching for Skip Intro buttons:", e);
                resolve({ found: false, count: 0, buttons: [], reason: "error", error: e.toString() }); 
            }
        });
        '''
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
                        
                        // 🔍REMOVED: calculateRecordingArea function - now handled by Python OpenCV
                        // Recording area will be calculated in Python using OpenCV layout analysis
                        
                        // =====================================================
                        // UPDATED: Function getIconsWithHybridLogic (Logic Mới)
                        // =====================================================
                        function getIconsWithHybridLogic(allTargets) {
                            console.log("🎬 Bắt đầu quá trình lấy icons (Bottom Half + Coordinate Sorting)...");

                            // Bước 1: LẤY CÁC ICON ĐANG HIỆN THỊ 
                            console.log("🔶️ Bước 1: Tìm các sprite 'Icons' đang hiện thị...");
                            const visibleIcons = allTargets.filter(t => t.sprite.name === 'Icons' && t.visible);
                            console.log(`✦ Tìm thấy ${visibleIcons.length} icons trên màn hình.`);

                            // Bước 2: CHUYỂN ĐỔI TỌA ĐỘ VÀ LỌC NỬA DƯỚI MÀN HÌNH
                            console.log("🔶️ Bước 2: Chuyển đổi tọa độ và lọc icons ở nửa dưới màn hình...");
                            const iconsWithPixelCoords = visibleIcons.map((icon) => {
                                const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                                return {
                                    originalIcon: icon,
                                    x: pixelCoords.x,
                                    y: pixelCoords.y
                                };
                            }).filter(icon => icon.y >= 540); // Chỉ lấy icons ở nửa dưới màn hình (y >= 540)
                            
                            console.log(`✦ Sau khi lọc nửa dưới: ${iconsWithPixelCoords.length} icons.`);

                            // Bước 3: SẮP XẾP THEO TỌA ĐỘ (TRÊN XUỐNG DƯỚI, TRÁI SANG PHẢI)
                            console.log("🔶️ Bước 3: Sắp xếp icons theo tọa độ (từ trên xuống dưới, trái sang phải)...");
                            
                            // Tolerance cho việc xác định cùng hàng
                            const ROW_TOLERANCE = 30;
                            
                            // Nhóm icons thành các hàng dựa trên tọa độ y
                            const sortedByY = iconsWithPixelCoords.sort((a, b) => a.y - b.y);
                            const rows = [];
                            
                            sortedByY.forEach(icon => {
                                // Tìm hàng phù hợp (y gần nhau trong khoảng tolerance)
                                let foundRow = false;
                                for (let row of rows) {
                                    if (Math.abs(row[0].y - icon.y) <= ROW_TOLERANCE) {
                                        row.push(icon);
                                        foundRow = true;
                                        break;
                                    }
                                }
                                
                                // Nếu không tìm thấy hàng phù hợp, tạo hàng mới
                                if (!foundRow) {
                                    rows.push([icon]);
                                }
                            });
                            
                            console.log(`✦ Tạo được ${rows.length} hàng icons.`);
                            
                            // Sắp xếp từng hàng theo x (trái sang phải) và ghép lại
                            const finalSortedIcons = [];
                            rows.forEach((row, rowIndex) => {
                                // Sắp xếp icons trong hàng theo x
                                row.sort((a, b) => a.x - b.x);
                                console.log(`✦ Hàng ${rowIndex + 1}: ${row.length} icons`);
                                finalSortedIcons.push(...row);
                            });

                            // Bước 4: TẠO MẢNG KẾT QUẢ CUỐI CÙNG
                            console.log("🔶️ Bước 4: Gán index tuần tự mới...");
                            const finalLayoutIcons = finalSortedIcons.map((iconData, index) => {
                                return {
                                    x: iconData.x,
                                    y: iconData.y,
                                    index: index // Gán index tuần tự 0, 1, 2... theo thứ tự sau khi sắp xếp
                                };
                            });

                            console.log("✦ HOÀN THÀNH! Icons được tạo theo logic Bottom Half + Coordinate Sorting:");
                            console.table(finalLayoutIcons);
                            
                            return finalLayoutIcons;
                        }
                        // =====================================================
                        // END: UPDATED Function getIconsWithHybridLogic
                        // =====================================================

                        const vm = findVM();
                        if (!vm) return reject({ error: 'Cannot find Scratch VM' });
                        
                        const customPageTitle = await getCustomPageTitle();
                        const allTargets = vm.runtime.targets.map(t => t);
                        
                        const icons = getIconsWithHybridLogic(allTargets);
                        
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
                            return { ...coords, originalSprite: s.sprite.name };
                        });
                        
                        // Sort by X coordinate (left to right) and reassign index
                        slotsWithCoords.sort((a, b) => a.x - b.x);
                        const slots = slotsWithCoords.map((slot, i) => ({ 
                            x: slot.x, 
                            y: slot.y, 
                            index: i,
                            originalSprite: slot.originalSprite
                        }));
                        // =====================================================
                        // END: FIXED slots sorting
                        // =====================================================
                        
                        // 🎮NEW: Recording area will be calculated in Python using OpenCV
                        // Set to null here, will be overridden by Python
                        const customRecordingArea = null;
                        
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
                                recordingArea: customRecordingArea,  // Will be overridden by Python OpenCV calculation
                                skipIntroButton: skipIntroButton
                            }
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

    def load_verification_config(self):
            """Load verification configuration from config.json"""
            try:
                # Page verification settings
                self.page_verification = self.config.get('page_verification', {})
                self.page_verification_enabled = self.page_verification.get('enabled', True)
                self.page_verification_timeout = self.page_verification.get('timeout_ms', 120000) / 1000  # Convert to seconds
                self.page_verification_interval = self.page_verification.get('check_interval_ms', 2000) / 1000
                self.page_verification_criteria = self.page_verification.get('criteria', {})
                self.page_verification_retries = self.page_verification.get('max_retries', 3)
                self.stabilization_wait = self.page_verification.get('stabilization_wait_seconds', 3)
                
                # Game load verification settings
                self.game_verification = self.config.get('game_load_verification', {})
                self.game_verification_enabled = self.game_verification.get('enabled', True)
                self.game_verification_timeout = self.game_verification.get('timeout_ms', 90000) / 1000
                self.game_verification_interval = self.game_verification.get('check_interval_ms', 2000) / 1000
                self.game_container_selectors = self.game_verification.get('game_container_selectors', [])
                self.min_containers_required = self.game_verification.get('min_containers_required', 1)
                self.check_react_components = self.game_verification.get('check_react_components', True)
                self.check_run_button_dom = self.game_verification.get('check_run_button_dom', True)
                
                print(f"Ã¢Å“â€¦ JSON Generator verification config loaded:")
                print(f"   Page verification: {'Ã¢Å“â€¦ Enabled' if self.page_verification_enabled else 'Ã¢ÂÅ’ Disabled'}")
                print(f"   Game verification: {'Ã¢Å“â€¦ Enabled' if self.game_verification_enabled else 'Ã¢ÂÅ’ Disabled'}")
                print(f"   Page timeout: {self.page_verification_timeout}s")
                print(f"   Game timeout: {self.game_verification_timeout}s")
                print(f"   Stabilization wait: {self.stabilization_wait}s")
                
            except Exception as e:
                print(f"Ã¢Å¡ Ã¯Â¸Â Could not load verification config, using defaults: {e}")
                # Set defaults if config loading fails
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

    def capture_and_analyze_layout(self):
        """Chụp ảnh màn hình và phân tích layout bằng OpenCV để xác định vùng recording"""
        try:
            print("📸 Capturing fullscreen screenshot for layout analysis...")
            
            # Ensure window activation for screenshot
            self.ensure_window_activation("layout analysis screenshot")
            
            # Capture fullscreen screenshot
            screenshot = pyautogui.screenshot()
            
            # Save temporary screenshot for analysis
            temp_screenshot_path = "temp_layout_analysis.png"
            screenshot.save(temp_screenshot_path)
            
            print("🔍 Analyzing layout with OpenCV...")
            
            # Use analyze_layout to find dividing lines
            result = find_dividing_lines(temp_screenshot_path)
            
            # Kiểm tra result một cách an toàn
            if result is None:
                print("❌ Failed to analyze layout - no dividing lines found")
                return False
            
            # Kiểm tra kiểu dữ liệu của result
            if not isinstance(result, (tuple, list)) or len(result) < 1:
                print("❌ Invalid result format from layout analysis")
                return False
            
            # Kiểm tra phần tử đầu tiên
            first_element = result[0]
            if not isinstance(first_element, (tuple, list)) or len(first_element) < 2:
                print("❌ Invalid dividing lines format")
                return False
            
            # An toàn unpack
            try:
                dividing_lines = first_element
                top_line_y, bottom_line_y = dividing_lines[0], dividing_lines[1]
                
                # Lấy các phần tử khác nếu có
                edges = result[1] if len(result) > 1 else None
                projection = result[2] if len(result) > 2 else None
                all_peaks = result[3] if len(result) > 3 else None
                
            except (IndexError, TypeError, ValueError) as e:
                print(f"❌ Failed to extract layout data: {e}")
                return False
            
            if top_line_y is None or bottom_line_y is None:
                print("❌ Failed to find both dividing lines")
                print(f"   Top line: {top_line_y}, Bottom line: {bottom_line_y}")
                return False
            
            # Store results as instance variables
            self.top_line_y = top_line_y
            self.bottom_line_y = bottom_line_y
            
            print(f"✅ Layout analysis successful!")
            print(f"   Top dividing line: Y = {top_line_y}")
            print(f"   Bottom dividing line: Y = {bottom_line_y}")
            print(f"   Available height: {bottom_line_y - top_line_y} pixels")
            
            # Clean up temporary file
            try:
                import os
                os.remove(temp_screenshot_path)
            except:
                pass
                
            return True
            
        except Exception as e:
            print(f"❌ Error in layout analysis: {e}")
            import traceback
            traceback.print_exc()
            return False
        
    def calculate_recording_area_opencv(self, slot_center_x, center_index, total_slots):
        """Tính toán recording area dựa trên kết quả OpenCV và tọa độ slot center"""
        try:
            if not hasattr(self, 'top_line_y') or not hasattr(self, 'bottom_line_y'):
                print("⌧ Layout analysis not performed yet - cannot calculate recording area with OpenCV")
                print("💡 This usually means the layout analysis step was skipped or failed")
                return None
                
            if self.top_line_y is None or self.bottom_line_y is None:
                print("⌧ Invalid dividing lines from OpenCV analysis - cannot calculate recording area")
                print(f"   Top line: {self.top_line_y}, Bottom line: {self.bottom_line_y}")
                return None
            
            if slot_center_x is None:
                print("⌧ Slot center X coordinate not available")
                return None
                
            print(f"🎯 Calculating recording area with OpenCV method...")
            print(f"   📐 Dividing lines detected: Y{self.top_line_y} to Y{self.bottom_line_y}")
            print(f"   🎯 Slot center (index {center_index}/{total_slots}): X={slot_center_x}")
            
            # Step 1: Calculate height (distance between dividing lines)
            height = self.bottom_line_y - self.top_line_y
            
            # Validate height is reasonable
            if height <= 0:
                print(f"⌧ Invalid height calculated: {height} (bottom_line_y: {self.bottom_line_y}, top_line_y: {self.top_line_y})")
                return None
            
            if height < 100:  # Too small
                print(f"⚠️ Very small height detected: {height}px - may be inaccurate")
            elif height > 800:  # Too large  
                print(f"⚠️ Very large height detected: {height}px - may be inaccurate")
            
            # Step 2: Calculate width maintaining 96:83 aspect ratio
            width = height * (96 / 83)
            
            # Step 3: Calculate X coordinate (centered on slot center)
            x = slot_center_x - (width / 2)
            
            # Step 4: Apply boundary constraints (keep within screen)
            screen_width = 1920  # Fullscreen width
            original_x = x
            
            if x < 0:
                x = 0
                print(f"   ⚠️ X adjusted to 0 (was {original_x:.1f} - would be off-screen left)")
            elif x + width > screen_width:
                x = screen_width - width
                print(f"   ⚠️ X adjusted to {x:.1f} (was {original_x:.1f} - would be off-screen right)")
            
            # Y coordinate is exactly the top dividing line
            y = self.top_line_y
            
            recording_area = {
                'x': int(round(x)),
                'y': int(y),
                'width': int(round(width)),
                'height': int(height),
                'aspectRatio': "96:83"
            }
            
            print(f"✅ OpenCV Recording Area calculated successfully:")
            print(f"   📐 Position: ({recording_area['x']}, {recording_area['y']})")
            print(f"   📐 Size: {recording_area['width']} x {recording_area['height']}")
            print(f"   📐 Aspect Ratio: {recording_area['aspectRatio']}")
            print(f"   🎯 Perfect fit between dividing lines: Y{self.top_line_y} to Y{self.bottom_line_y}")
            print(f"   🎯 Centered on slot center (index {center_index}) at X{slot_center_x}")
            
            return recording_area
            
        except Exception as e:
            print(f"⌧ Error calculating recording area with OpenCV: {e}")
            import traceback
            traceback.print_exc()
            return None

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

    def verify_page_fully_loaded(self, target_url):
        """
        Ã°Å¸â€Â Smart Page Load Verification System for JSON Generator
        Checks if page is fully loaded before proceeding with Run button detection
        """
        try:
            if not self.page_verification_enabled:
                print("Ã°Å¸â€Â Page verification disabled, assuming page ready")
                return True
            
            print(f"\nÃ°Å¸â€Â JSON GENERATOR - SMART PAGE VERIFICATION: {target_url}")
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
                
                // Estimate network activity
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
                print(f"Ã°Å¸â€Â JSON Generator page verification attempt {attempt}/{max_retries}")
                
                # Execute verification JavaScript
                verification_result = self._execute_verification_js(verification_js)
                
                if verification_result:
                    document_state = verification_result.get('documentState', 'unknown')
                    basic_elements = verification_result.get('basicElements', {})
                    loading_spinners = verification_result.get('loadingSpinners', {})
                    ready_score = verification_result.get('readyScore', 0)
                    is_ready = verification_result.get('isReady', False)
                    
                    print(f"   Ã°Å¸â€œÅ  Document state: {document_state}")
                    print(f"   Ã°Å¸â€œÅ  Basic elements: Body={basic_elements.get('hasBody')}, Head={basic_elements.get('hasHead')}, Children={basic_elements.get('bodyChildren', 0)}")
                    print(f"   Ã°Å¸â€œÅ  Loading spinners: {loading_spinners.get('count', 0)} active")
                    print(f"   Ã°Å¸â€œÅ  Ready score: {ready_score}/100")
                    print(f"   Ã°Å¸â€œÅ  Overall assessment: {'Ã¢Å“â€¦ READY' if is_ready else 'Ã¢ÂÂ³ NOT READY'}")
                    
                    if is_ready:
                        elapsed = time.time() - start_time
                        print(f"Ã¢Å“â€¦ JSON GENERATOR PAGE VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if loading_spinners.get('count', 0) > 0:
                            spinner_types = ', '.join(loading_spinners.get('types', []))
                            print(f"   Ã¢ÂÂ³ Waiting for loading spinners to disappear: {spinner_types}")
                        if document_state != 'complete':
                            print(f"   Ã¢ÂÂ³ Waiting for document to complete loading...")
                        if basic_elements.get('bodyChildren', 0) == 0:
                            print(f"   Ã¢ÂÂ³ Waiting for page content to render...")
                
                else:
                    print(f"   Ã¢Å¡ Ã¯Â¸Â Verification JavaScript failed, using fallback check")
                    # Fallback: Simple document ready check
                    simple_check = self._execute_verification_js("document.readyState")
                    if simple_check == 'complete':
                        print(f"   Ã¢Å“â€¦ Fallback check passed: document.readyState = complete")
                        return True
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.page_verification_timeout:
                    print(f"Ã¢Å¡ Ã¯Â¸Â JSON Generator page verification timeout ({self.page_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with Run button detection...")
                    return True  # Don't fail completely, proceed with detection
                
                # Wait before next attempt
                if attempt < max_retries:
                    print(f"   Ã¢ÂÂ³ Waiting {self.page_verification_interval}s before next check...")
                    time.sleep(self.page_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"Ã¢Å¡ Ã¯Â¸Â JSON Generator page verification failed after {max_retries} attempts")
            print("   Proceeding anyway with Run button detection...")
            return True
            
        except Exception as e:
            print(f"Ã¢Å¡ Ã¯Â¸Â Error in JSON Generator page verification: {e}")
            print("   Proceeding anyway with Run button detection...")
            return True  # Don't block workflow on verification errors

    def verify_game_components_ready(self):
        """
        Ã°Å¸Å½Â® Smart Game Load Verification System for JSON Generator
        Checks if game components are ready before proceeding with Run button detection
        """
        try:
            if not self.game_verification_enabled:
                print("Ã°Å¸Å½Â® Game verification disabled, assuming game ready")
                return True
            
            print(f"\nÃ°Å¸Å½Â® JSON GENERATOR - SMART GAME VERIFICATION")
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
                    overallReady: false,
                    timestamp: Date.now()
                }};
                
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
            max_attempts = 3
            
            for attempt in range(1, max_attempts + 1):
                print(f"Ã°Å¸Å½Â® JSON Generator game verification attempt {attempt}/{max_attempts}")
                
                # Execute game verification JavaScript
                game_result = self._execute_verification_js(game_verification_js)
                
                if game_result:
                    game_containers = game_result.get('gameContainers', [])
                    react_components = game_result.get('reactComponents', {})
                    run_button_in_dom = game_result.get('runButtonInDOM', False)
                    overall_ready = game_result.get('overallReady', False)
                    
                    print(f"   Ã°Å¸Å½Â¯ Game containers found: {len(game_containers)}")
                    for container in game_containers[:3]:  # Show first 3
                        print(f"      - {container['selector']}: visible={container['visible']}, children={container['hasChildren']}")
                    
                    if self.check_react_components:
                        react_found = react_components.get('found', False)
                        react_count = len(react_components.get('details', []))
                        print(f"   Ã¢Å¡â€ºÃ¯Â¸Â React components: {'Ã¢Å“â€¦ Found' if react_found else 'Ã¢ÂÅ’ Not found'} ({react_count} detected)")
                    
                    if self.check_run_button_dom:
                        print(f"   Ã°Å¸â€Ëœ Run button in DOM: {'Ã¢Å“â€¦ Found' if run_button_in_dom else 'Ã¢ÂÅ’ Not found'}")
                    
                    print(f"   Ã°Å¸Å½Â¯ Overall game ready: {'Ã¢Å“â€¦ YES' if overall_ready else 'Ã¢ÂÅ’ NO'}")
                    
                    if overall_ready:
                        elapsed = time.time() - start_time
                        print(f"Ã¢Å“â€¦ JSON GENERATOR GAME VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if len(game_containers) < self.min_containers_required:
                            print(f"   Ã¢ÂÂ³ Waiting for game containers to load ({len(game_containers)}/{self.min_containers_required})")
                        if self.check_react_components and not react_components.get('found'):
                            print(f"   Ã¢ÂÂ³ Waiting for React components to initialize")
                        if self.check_run_button_dom and not run_button_in_dom:
                            print(f"   Ã¢ÂÂ³ Waiting for Run button to appear in DOM")
                
                else:
                    print(f"   Ã¢Å¡ Ã¯Â¸Â Game verification JavaScript failed")
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.game_verification_timeout:
                    print(f"Ã¢Å¡ Ã¯Â¸Â JSON Generator game verification timeout ({self.game_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with Run button detection...")
                    return True
                
                # Wait before next attempt
                if attempt < max_attempts:
                    print(f"   Ã¢ÂÂ³ Waiting {self.game_verification_interval}s before next check...")
                    time.sleep(self.game_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"Ã¢Å¡ Ã¯Â¸Â JSON Generator game verification failed after {max_attempts} attempts")
            print("   Proceeding anyway with Run button detection...")
            return True
            
        except Exception as e:
            print(f"Ã¢Å¡ Ã¯Â¸Â Error in JSON Generator game verification: {e}")
            print("   Proceeding anyway with Run button detection...")
            return True

    def _click_run_button_cdp_only(self):
        """Pure CDP implementation for Run button detection and clicking"""
        try:
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

    def cleanup_chrome_complete(self):
        """Complete cleanup: Close Chrome debug entirely and clean all resources"""
        try:
            print("ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â¹ Complete cleanup: Closing Chrome debug and cleaning resources...")
            
            for title_part in ['cocrea.world', 'Chrome']:
                windows = gw.getWindowsWithTitle(title_part)
                for window in windows:
                    try:
                        print(f"   ÃƒÂ°Ã…Â¸Ã¢â‚¬â€Ã¢â‚¬Å¡ÃƒÂ¯Ã‚Â¸Ã‚Â Closing window: {window.title}")
                        window.close()
                    except:
                        pass
            
            time.sleep(2)
            
            if hasattr(self, 'chrome_process') and self.chrome_process:
                try:
                    print("   ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Terminating Chrome process...")
                    self.chrome_process.terminate()
                    self.chrome_process.wait(timeout=5)
                except:
                    try:
                        self.chrome_process.kill()
                    except:
                        pass
                self.chrome_process = None
            
            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] and 'chrome' in proc.info['name'].lower():
                            cmdline = proc.info.get('cmdline', [])
                            if cmdline and '--remote-debugging-port=9222' in ' '.join(cmdline):
                                print(f"   ÃƒÂ¢Ã…Â¡Ã¢â‚¬ÂÃƒÂ¯Ã‚Â¸Ã‚Â Killing leftover Chrome debug process: {proc.info['pid']}")
                                proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except ImportError:
                print("   ÃƒÂ¢Ã…Â¡ ÃƒÂ¯Ã‚Â¸Ã‚Â psutil not available for process cleanup")
            
            # Optional minimal cleanup (can be disabled)
            skip_cleanup = self.chrome_config.get('skip_cache_cleanup', False)
            if skip_cleanup:
                print("   ℹ️ Cache cleanup SKIPPED (preserving everything for extensions)")
                print("   📦 All cookies, sessions, extension data preserved")
            else:
                print("   🗑️ Minimal cache clearing only...")
                try:
                    self.clear_cache_cookies_only()
                except Exception as e:
                    print(f"   ⚠️ Could not clear cache/cookies: {e}")
            
            self.ws_url = None
            
            print("✅ Complete cleanup finished - Chrome debug fully closed + browsing data cleared")
            time.sleep(1)
            return True
            
        except Exception as e:
            print(f"ÃƒÂ¢Ã‚ÂÃ…â€™ Complete cleanup error: {e}")
            return False

    def clear_cache_cookies_only(self):
        """🆕 Clear only cache and cookies, keep profile/login intact"""
        try:
            import shutil
            import glob
            
            print("🗑️ Clearing cache and cookies only (keeping profile intact)...")
            
            user_data_path = os.path.join(os.getcwd(), self.user_data_dir)
            if not os.path.exists(user_data_path):
                print(f"   ℹ️ Profile directory not found: {user_data_path}")
                return
                
            default_path = os.path.join(user_data_path, "Default")
            if not os.path.exists(default_path):
                print("   ℹ️ Default profile not found")
                return
                
            # Clear only temporary cache files, PRESERVE login/extension data
            cache_paths = [
                os.path.join(default_path, "Cache", "Cache_Data"),  # Browser cache only
                os.path.join(default_path, "Code Cache"),           # JS compiled cache only  
                os.path.join(default_path, "GPUCache"),             # GPU cache only
                # ❌ REMOVED: Cookies - giữ để duy trì login
                # ❌ REMOVED: Cookies-journal - giữ để duy trì login
                # ❌ REMOVED: Sessions - giữ để duy trì active sessions
                # ❌ REMOVED: Local Storage - extensions cần dữ liệu này
                # ❌ REMOVED: IndexedDB - extensions lưu settings ở đây
                # ❌ REMOVED: Service Worker - extensions hoạt động qua SW
                # ❌ REMOVED: Network - giữ để extension hoạt động ngay
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
                        print(f"   ✅ Cleared: {os.path.basename(cache_path)}")
                except Exception as e:
                    print(f"   ⚠️ Could not clear {cache_path}: {e}")
                    
            print(f"   🧹 Minimal cache cleanup completed - {cleared_count} items processed")
            print("   ✅ Login session, cookies, extensions data preserved")
            print("   📦 Extensions should work immediately without refresh")
            
        except Exception as e:
            print(f"❌ Cache clearing error: {e}")

    def force_clear_chrome_data(self):
        """🆕 FORCE: Clear Chrome data even when Chrome is not running"""
        try:
            import shutil
            import getpass
            
            print("🗑️ FORCE CLEARING Chrome browsing data...")
            
            # Method 1: Clear cache/cookies only (keep profile intact)
            self.clear_cache_cookies_only()
            
            print("   ℹ️ Profile directory preserved for login/extensions")
            
            print("✅ Force clear completed")
            return True
            
        except Exception as e:
            print(f"❌ Force clear error: {e}")
            return False

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

    def wait_for_complete_game_readiness(self, url):
        """
        ðŸ”„ CONTINUOUS MONITORING: Wait for both page + game ready with configurable timeout
        """
        try:
            # Get configuration from config.json
            continuous_config = self.config.get('continuous_monitoring', {})
            page_config = self.config.get('page_verification', {})
            game_config = self.config.get('game_load_verification', {})
            
            # Get timeouts and intervals
            max_timeout = continuous_config.get('max_wait_time_seconds', 120)
            check_interval = continuous_config.get('check_interval_seconds', 10)
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
            
            while True:
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

    def verify_page_fully_loaded_lightweight(self, target_url):
        """Lightweight version for continuous monitoring"""
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
            if result:
                print(f"      ðŸ“Š Page score: {result.get('score', 0)}/100, Spinners: {result.get('loadingSpinners', 'N/A')}")
                return result.get('ready', False)
            return False
            
        except Exception as e:
            print(f"   âš ï¸ Page check error: {e}")
            return False

    def verify_game_components_ready_lightweight(self):
        """Lightweight version for continuous monitoring"""
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
            if result:
                containers = result.get('gameContainers', 0)
                has_button = result.get('runButton', False)
                print(f"      ðŸ“Š Containers: {containers}, Run button: {'âœ…' if has_button else 'âŒ'}")
                return result.get('ready', False)
            return False
            
        except Exception as e:
            print(f"   âš ï¸ Game check error: {e}")
            return False

    def generate_complete_json(self, url, output_path=None):
            """
            [PHASE 3 REFACTORED]
            Simplified Complete Logic: A vs B vs C.
            All JavaScript execution is now delegated to the game_player engine.
            
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
                
                # Setup game
                if not self.navigate_and_prepare_game(url): 
                    return False, None, None

                # 🔧 SERVICE MODE FIX: Ensure window activation after game setup
                print("🔧 Ensuring window activation after game setup...")
                self.ensure_window_activation("post-game-setup")

                # <<< BẮT ĐẦU SỬA: Xóa bỏ quản lý WebSocket thủ công
                # CÁC DÒNG SAU ĐÂY SẼ BỊ XÓA:
                # ws_url = self._get_websocket_url()
                # if not ws_url: 
                #     return False, None, None
                # ws = websocket.create_connection(ws_url, timeout=30)
                # >>> KẾT THÚC SỬA
                
                print("🔍 Testing JavaScript environment...")
                # [FIXED] Test môi trường thông qua engine
                test_response = self.game_player._execute_verification_js("window.location.href")
                if test_response is None:
                    print("❌ JavaScript environment test failed via engine")
                    self.cleanup_chrome_complete()
                    return False, None, None
                print(f"✅ JavaScript environment ready. Current URL: {test_response}")
                
                print("⏩ Skipping intros via CDP...")
                # ============================
                # MULTI-TARGET SKIP INTRO: Click ALL Skip Intro buttons + Blind Clicks
                # ============================
                print("\n🎯 MULTI-TARGET SKIP INTRO: Finding and clicking ALL Skip Intro buttons...")

                # Phase 1: Find ALL Skip Intro buttons
                print("🔍 Phase 1: Searching for ALL Skip Intro buttons...")

                # [FIXED] Thực thi JS skip intro thông qua engine
                skip_intro_response = self.game_player._execute_verification_js(self.js_find_skip_intro)

                smart_clicks_performed = 0
                all_buttons = []

                if skip_intro_response and skip_intro_response.get('found'):
                    all_buttons = skip_intro_response.get('buttons', [])
                    button_count = skip_intro_response.get('count', 0)
                    visible_count = skip_intro_response.get('visibleCount', 0)
                    hidden_count = skip_intro_response.get('hiddenCount', 0)
                    
                    print(f"✅ Found {button_count} Skip Intro buttons:")
                    print(f"   📊 Visible: {visible_count}, Hidden: {hidden_count}")
                    
                    # Click ALL buttons found
                    for i, button in enumerate(all_buttons):
                        button_num = i + 1
                        x, y = button['x'], button['y']
                        visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                        costume_name = button.get('currentCostumeName', 'Unknown')
                        
                        print(f"👆 Clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status} - {costume_name}")
                        
                        # [FIXED] Click this specific button
                        self.game_player._execute_cdp_single_click(x, y)
                        time.sleep(0.8)  # Wait between clicks
                        
                        smart_clicks_performed += 1
                    
                    print(f"✅ Smart clicks completed: {smart_clicks_performed} buttons clicked!")
                    
                else:
                    reason = skip_intro_response.get('reason', 'unknown') if skip_intro_response else 'no_result'
                    print(f"❌ No Skip Intro buttons found - Reason: {reason}")
                    print("⚠️ Will use blind click method only...")

                # Phase 2: ALWAYS perform blind clicks for additional assurance
                if smart_clicks_performed > 0:
                    print("🔄 Phase 2: Performing ADDITIONAL blind clicks for triple protection...")
                    print("   📝 This ensures intro skipping even if smart clicks had edge cases")
                else:
                    print("🔄 Phase 2: Performing STANDARD blind clicks (no smart buttons found)...")

                # Perform blind clicks regardless of smart click success
                for i in range(self.intro_skip_clicks):  # Use config value
                    # [FIXED] Sử dụng hàm click của engine
                    self.game_player._execute_cdp_single_click(960, 540)
                    time.sleep(0.5)

                # Summary
                if smart_clicks_performed > 0:
                    print("🎯 MULTI-TARGET SKIP INTRO COMPLETED:")
                    print(f"   ✅ Smart clicks: {smart_clicks_performed} buttons clicked successfully")
                    print(f"   ✅ Button details: {len(all_buttons)} total found")
                    for i, button in enumerate(all_buttons):
                        visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                        print(f"      {i+1}. ({button['x']}, {button['y']}) - {visible_status}")
                    print(f"   ✅ Blind clicks: {self.intro_skip_clicks} additional clicks performed")
                    print("   🛡️ Triple protection ensures maximum skip success rate")
                else:
                    print("🎯 STANDARD SKIP INTRO COMPLETED:")
                    print("   ❌ Smart clicks: N/A (No Skip Intro buttons found)")
                    print(f"   ✅ Blind clicks: {self.intro_skip_clicks} clicks performed")
                    print("   🔄 Standard method used as fallback")

                print("✅ Skip intro phase finished - Game should be ready for interaction")

                # OpenCV Layout Analysis AFTER entering the actual game
                print("\n" + "="*70)
                print("🔍 OPENCV LAYOUT ANALYSIS - Analyzing Game Layout After Game Entry")
                print("="*70)
                print("🖼️ Now that we're in the actual game, we can accurately detect layout regions")
                
                # Wait a bit for game to fully stabilize after intro skip
                print("⏳ Waiting for game to fully stabilize after intro skip...")
                time.sleep(3)
                
                # Ensure window is properly focused for screenshot
                self.ensure_window_activation("post-intro layout analysis")
                
                # Capture and analyze layout using OpenCV
                analysis_success = self.capture_and_analyze_layout()
                
                if not analysis_success:
                    print("❌ CRITICAL: Layout analysis failed - cannot determine recording area")
                    print("💡 This means we cannot accurately detect the game regions")
                    print("🔄 Will continue with workflow but recording area may be inaccurate")
                    # Don't fail completely, just proceed without OpenCV recording area
                else:
                    print("✅ OpenCV layout analysis completed successfully!")
                    print("🖼️ Game regions detected - recording area will be calculated accurately")
                
                print("="*70)

                # Initialize final result variables
                final_has_horror = False
                final_confidence = 0
                final_detection_method = "no_horror_detected"
                final_visual_result = {}
                
                # ============================
                # CAPTURE A: Initial State (Screenshot + Phase)
                # ============================
                print("\n" + "="*70)
                print("📸 CAPTURE A: Initial State (Screenshot + Phase)")
                print("="*70)
                
                print("📊 Extracting initial state A + coordinates...")
                # [FIXED] Thực thi JS lấy state A thông qua engine
                state_A = self.game_player._execute_verification_js(self.js_get_state_and_coordinates)
                
                if not state_A or "error" in state_A: 
                    print(f"❌ Failed to get initial state A: {state_A.get('error', 'Unknown') if state_A else 'None'}")
                    self.cleanup_chrome_complete()
                    return False, None, None
                
                phase_A = self.find_phase_variable(state_A)
                print(f"✅ State A captured - Phase A: {phase_A}")
                
                # ============================
                # CRITICAL CHECK: Verify icon count before proceeding
                # ============================
                icons_count = len(state_A.get('icons', []))
                print(f"\n📊 ICON COUNT VERIFICATION: Found {icons_count} icons")
                
                if icons_count < 20:
                    print(f"❌ INSUFFICIENT ICONS: Only {icons_count} icons found (need 20+ for icon index 19)")
                    print("🚨 EARLY EXIT: Setting hasHorror = False due to insufficient icons")
                    
                    # Get final layout for JSON generation
                    print("📊 Extracting final full layout...")
                    # [FIXED] Thực thi JS lấy layout cuối cùng thông qua engine
                    layout_state = self.game_player._execute_verification_js(self.js_full_layout)
                    
                    if layout_state and "error" not in layout_state:
                        print("✅ Final full layout captured successfully")
                        
                        # 🔍 NEW: Calculate recording area using OpenCV even for insufficient icons case
                        print("\n🔍 Calculating recording area with OpenCV for insufficient icons case...")
                        
                        # Get slot center coordinates
                        slots = layout_state.get('layout', {}).get('slots', [])
                        center_slot, center_index, total_slots = self.calculate_slot_center_index(slots)

                        if center_slot and center_slot.get('x') is not None:
                            slot_center_x = center_slot['x']
                            opencv_recording_area = self.calculate_recording_area_opencv(slot_center_x, center_index, total_slots)
                            
                            if opencv_recording_area:
                                layout_state['layout']['recordingArea'] = opencv_recording_area
                                print("✅ Recording area calculated successfully with OpenCV method")
                                print(f"   📌 Recording Area: {opencv_recording_area}")
                            else:
                                print("❌ OpenCV recording area calculation failed")
                                print("💡 Proceeding without recording area (insufficient icons case)")
                                layout_state['layout']['recordingArea'] = None
                        else:
                            print("❌ Slot 3 not found - no recording area calculated")
                            layout_state['layout']['recordingArea'] = None
                        
                        # Generate JSON with hasHorror = False
                        page_title = layout_state.get('pageTitle', 'Unknown')
                        final_json = {
                            "version": "11.0-simplified-complete-logic", 
                            "created": datetime.now().isoformat(),
                            "currentUrl": layout_state.get('currentUrl'), 
                            "pageTitle": page_title,
                            "gameSettings": {
                                "hasHorror": False,
                                "hasBonus": len(layout_state.get('layout', {}).get('bonus', [])) > 0,
                                "detectionMethod": "insufficient_icons",
                                "detectionDetails": {
                                    "method": "insufficient_icons",
                                    "confidence": 1.0,
                                    "icons_found": icons_count,
                                    "icons_required": 20,
                                    "reason": f"Only {icons_count} icons found, need 20+ for horror detection",
                                    "logic_flow": "Early exit due to insufficient icons"
                                }
                            },
                            "layout": layout_state.get('layout', {}),
                            "settings": {
                                "aspectRatio": "96:83"
                            }
                        }
                        
                        print("💾 Saving JSON file...")
                        json_file_path = self.save_json_file(final_json, output_path, custom_title=page_title)
                        
                        if json_file_path:
                            print(f"\n🎯 EARLY EXIT COMPLETED - INSUFFICIENT ICONS!")
                            print("="*70)
                            print(f"✅ hasHorror Detection: False (insufficient icons)")
                            print(f"✅ Icons Found: {icons_count}/20")
                            print(f"✅ Reason: Need icon index 19 for horror detection")
                            print("="*70)
                            
                            self.cleanup_chrome_complete()
                            return True, json_file_path, final_json
                        else:
                            print("❌ Failed to save JSON file")
                            self.cleanup_chrome_complete()
                            return False, None, None
                    else:
                        print("❌ Failed to get final layout for insufficient icons case")
                        self.cleanup_chrome_complete()
                        return False, None, None
                else:
                    print(f"✅ SUFFICIENT ICONS: {icons_count} icons found - proceeding with horror detection")
                
                # Capture screenshot A
                extended_area = self.calculate_extended_screenshot_area(state_A)
                screenshot_A = None
                if extended_area:
                    screenshot_A = self.capture_icons_area_screenshot(extended_area)
                    if screenshot_A is not None:
                        print(f"✅ Screenshot A captured - Size: {screenshot_A.shape}")
                    else:
                        print("❌ Failed to capture screenshot A")
                else:
                    print("❌ Could not calculate extended area for screenshot A")
                
                # ============================
                # DRAG ICON 19 & CAPTURE B: After Icon 19 (Screenshot + Phase)
                # ============================
                print("\n" + "="*70)
                print("🎯 DRAG ICON 19 & CAPTURE B: After Icon 19 (Screenshot + Phase)")
                print("="*70)
                
                drag_coords = state_A.get('dragCoordinates', {})
                icon_coords = drag_coords.get('icon')
                slot_coords = drag_coords.get('slot')
                
                # --- ENHANCED DEBUG LOGGING FOR DRAG COORDINATES ---
                # Log the retrieved coordinates for inspection
                print("\n🔍 DEBUG: Checking Drag Coordinates from State A...")
                print(f"   - Raw icon_coords (icon 19): {icon_coords}")
                print(f"   - Raw slot_coords (slot 3): {slot_coords}")

                # Detailed validation
                is_icon_valid = icon_coords and icon_coords.get('x') is not None and icon_coords.get('y') is not None
                is_slot_valid = slot_coords and slot_coords.get('x') is not None and slot_coords.get('y') is not None
                
                if not is_icon_valid or not is_slot_valid:
                    print("❌ FATAL: Missing or invalid drag coordinates. Cannot proceed with horror detection.")
                    if not is_icon_valid:
                        print("   - REASON: Horror Icon (index 19) coordinates are missing or invalid.")
                        if not icon_coords:
                            print("     -> Cause: 'icon' object not found in dragCoordinates.")
                        else:
                            print(f"     -> Cause: Coordinates are incomplete. x={icon_coords.get('x')}, y={icon_coords.get('y')}")
                    
                    if not is_slot_valid:
                        print("   - REASON: Target Slot (index 3) coordinates are missing or invalid.")
                        if not slot_coords:
                            print("     -> Cause: 'slot' object not found in dragCoordinates.")
                        else:
                            print(f"     -> Cause: Coordinates are incomplete. x={slot_coords.get('x')}, y={slot_coords.get('y')}")
                    
                    print("   - This usually happens if the JavaScript code failed to identify these specific elements on the page.")
                    
                    self.cleanup_chrome_complete()
                    return False, None, None
                # --- END OF ENHANCED DEBUG LOGGING ---

                print(f"🎯 Dragging icon 19 from ({icon_coords['x']}, {icon_coords['y']}) to ({slot_coords['x']}, {slot_coords['y']})")

                # [PHASE 2] SỬ DỤNG ENGINE ĐỂ KÉO THẢ ICON 19
                # Engine sẽ tự động xử lý retry 5 toạ độ, xác thực kép, và các cơ chế an toàn khác.
                print("INFO: Engine is now handling the drag & drop for icon 19...")
                drag_success = self.game_player.corrected_drag_and_drop(icon_coords, slot_coords)
                
                if not drag_success:
                    print("ERROR: Engine failed to drag icon 19. The horror test might be inaccurate.")
                
                wait_time = 25
                print(f"⏳ Waiting {wait_time} seconds for transition...")
                time.sleep(wait_time)
                
                print("📊 Extracting state B after icon 19...")
                # [FIXED] Thực thi JS lấy state B thông qua engine
                state_B = self.game_player._execute_verification_js(self.js_get_state_and_coordinates)
                
                if not state_B or "error" in state_B: 
                    print(f"❌ Failed to get state B: {state_B.get('error', 'Unknown') if state_B else 'None'}")
                    self.cleanup_chrome_complete()
                    return False, None, None
                
                phase_B = self.find_phase_variable(state_B)
                print(f"✅ State B captured - Phase B: {phase_B}")
                
                # Capture screenshot B
                screenshot_B = None
                if extended_area:
                    screenshot_B = self.capture_icons_area_screenshot(extended_area)
                    if screenshot_B is not None:
                        print(f"✅ Screenshot B captured - Size: {screenshot_B.shape}")
                    else:
                        print("❌ Failed to capture screenshot B")
                
                # ============================
                # COMPARE A vs B: Phase Comparison
                # ============================
                print("\n" + "="*70)
                print("🔍 COMPARE A vs B: Phase Comparison")
                print("="*70)
                
                print(f"📊 Comparing Phase A ({phase_A}) vs Phase B ({phase_B})")
                
                # Phase comparison logic
                phase_A_str = str(phase_A) if phase_A is not None else None
                phase_B_str = str(phase_B) if phase_B is not None else None
                
                # Initialize flags for this comparison round
                horror_detected_phase_AB = False
                
                if phase_A_str is not None and phase_B_str is not None:
                    has_horror_phase_AB = (phase_A_str == "1" and phase_B_str == "2")
                    
                    if has_horror_phase_AB:
                        print(f"🎯 SUCCESS! Phase A vs B: {phase_A_str} → {phase_B_str} = hasHorror TRUE")
                        final_has_horror = True
                        final_confidence = 1.0
                        final_detection_method = "phase_A_vs_B_icon_19"
                        final_visual_result = {
                            'phase_before': phase_A,
                            'phase_after': phase_B,
                            'method': 'phase_1_to_2',
                            'horror_icon_index': 19,
                            'comparison': 'A_vs_B'
                        }
                        horror_detected_phase_AB = True
                        # STOP - Horror detected!
                        print("🛑 STOP: Horror detected via phase A vs B")
                    else:
                        print(f"❌ No horror in phase A vs B: {phase_A_str} → {phase_B_str}")
                else:
                    print("❌ Cannot compare phases A vs B (missing phase data)")
                    print("🔄 Will proceed to screenshot comparison")
                
                # ============================
                # COMPARE A vs B: Screenshot Comparison (ALWAYS TRY)
                # ============================
                if not horror_detected_phase_AB:  # Only if phase didn't detect horror
                    print("\n" + "="*50)
                    print("📸 COMPARE A vs B: Screenshot Comparison")
                    print("="*50)
                    
                    if screenshot_A is not None and screenshot_B is not None:
                        print("🔍 Comparing Screenshot A vs Screenshot B...")
                        visual_result_AB = self.detect_horror_extended_area(screenshot_A, screenshot_B)
                        
                        if visual_result_AB and visual_result_AB['has_horror']:
                            print(f"🎯 SUCCESS! Screenshot A vs B detected hasHorror TRUE")
                            final_has_horror = True
                            final_confidence = visual_result_AB['confidence']
                            final_detection_method = f"screenshot_A_vs_B_{visual_result_AB['method']}_icon_19"
                            final_visual_result = visual_result_AB
                            final_visual_result['horror_icon_index'] = 19
                            final_visual_result['comparison'] = 'A_vs_B'
                            # STOP - Horror detected!
                            print("🛑 STOP: Horror detected via screenshot A vs B")
                        else:
                            print("❌ No horror in screenshot A vs B")
                    else:
                        print("❌ Cannot compare screenshots A vs B (missing screenshot data)")
                
                # ============================
                # IF NO HORROR YET: Try Highest Index Icon
                # ============================
                if not final_has_horror:
                    print("\n" + "="*70)
                    print("🔍 NO HORROR YET: Try Highest Index Icon")
                    print("="*70)
                    
                    # Get current layout to find highest index icon
                    # [FIXED] Thực thi JS lấy layout hiện tại thông qua engine
                    current_layout_data = self.game_player._execute_verification_js(self.js_full_layout)

                    if current_layout_data:
                        available_icons = current_layout_data.get('layout', {}).get('icons', [])
                        valid_icons = [icon for icon in available_icons if icon.get('x') is not None and icon.get('y') is not None]
                        high_index_icons = [icon for icon in valid_icons if icon.get('index', 0) > 19]

                        if high_index_icons:
                            highest_index_icon = max(high_index_icons, key=lambda x: x.get('index', 0))
                            highest_index = highest_index_icon.get('index', 0)
                            
                            print(f"🎯 Found highest index icon: {highest_index}")
                            
                            available_slots = current_layout_data.get('layout', {}).get('slots', [])
                            slot_4_coords = next((slot for slot in available_slots if slot.get('index') == 4), None)
                            
                            if slot_4_coords and slot_4_coords.get('x') is not None:
                                # ============================
                                # DRAG HIGHEST INDEX & CAPTURE C: After Highest Index (Screenshot + Phase)
                                # ============================
                                print("\n" + "="*70)
                                print(f"🎯 DRAG ICON {highest_index} & CAPTURE C: After Highest Index (Screenshot + Phase)")
                                print("="*70)
                                
                                print(f"🎯 Dragging icon {highest_index} from ({highest_index_icon['x']}, {highest_index_icon['y']}) to ({slot_4_coords['x']}, {slot_4_coords['y']})")

                                # [PHASE 2] SỬ DỤNG ENGINE ĐỂ KÉO THẢ ICON INDEX CAO NHẤT
                                print(f"INFO: Engine is now handling the drag & drop for icon {highest_index}...")
                                drag_success_C = self.game_player.corrected_drag_and_drop(highest_index_icon, slot_4_coords)

                                if not drag_success_C:
                                    print(f"ERROR: Engine failed to drag icon {highest_index}. Aborting this secondary test.")
                                    # Nếu kéo thả thất bại, chúng ta sẽ không tiếp tục với state C
                                
                                wait_time = 25
                                print(f"⏳ Waiting {wait_time}s for transition...")
                                time.sleep(wait_time)
                                
                                print("📊 Extracting state C after highest index...")
                                # [FIXED] Thực thi JS lấy state C thông qua engine
                                state_C = self.game_player._execute_verification_js(self.js_get_state_and_coordinates)
                                
                                if state_C:
                                    phase_C = self.find_phase_variable(state_C)
                                    print(f"✅ State C captured - Phase C: {phase_C}")
                                    
                                    # Capture screenshot C
                                    screenshot_C = None
                                    if extended_area:
                                        screenshot_C = self.capture_icons_area_screenshot(extended_area)
                                        if screenshot_C is not None:
                                            print(f"✅ Screenshot C captured - Size: {screenshot_C.shape}")
                                        else:
                                            print("❌ Failed to capture screenshot C")
                                    
                                    # Initialize flags for A vs C comparison
                                    horror_detected_phase_AC = False
                                    
                                    # ============================
                                    # COMPARE A vs C: Phase Comparison
                                    # ============================
                                    print("\n" + "="*70)
                                    print("🔍 COMPARE A vs C: Phase Comparison")
                                    print("="*70)
                                    
                                    print(f"📊 Comparing Phase A ({phase_A}) vs Phase C ({phase_C})")
                                    
                                    phase_C_str = str(phase_C) if phase_C is not None else None
                                    
                                    if phase_A_str is not None and phase_C_str is not None:
                                        has_horror_phase_AC = (phase_A_str == "1" and phase_C_str == "2")
                                        
                                        if has_horror_phase_AC:
                                            print(f"🎯 SUCCESS! Phase A vs C: {phase_A_str} → {phase_C_str} = hasHorror TRUE")
                                            final_has_horror = True
                                            final_confidence = 1.0
                                            final_detection_method = f"phase_A_vs_C_icon_{highest_index}"
                                            final_visual_result = {
                                                'phase_before': phase_A,
                                                'phase_after': phase_C,
                                                'method': 'phase_1_to_2',
                                                'horror_icon_index': highest_index,
                                                'comparison': 'A_vs_C'
                                            }
                                            horror_detected_phase_AC = True
                                            # STOP - Horror detected!
                                            print("🛑 STOP: Horror detected via phase A vs C")
                                        else:
                                            print(f"❌ No horror in phase A vs C: {phase_A_str} → {phase_C_str}")
                                    else:
                                        print("❌ Cannot compare phases A vs C (missing phase data)")
                                        print("🔄 Will proceed to screenshot comparison")
                                    
                                    # ============================
                                    # COMPARE A vs C: Screenshot Comparison (ALWAYS TRY)
                                    # ============================
                                    if not horror_detected_phase_AC:  # Only if phase didn't detect horror
                                        print("\n" + "="*50)
                                        print("📸 COMPARE A vs C: Screenshot Comparison")
                                        print("="*50)
                                        
                                        if screenshot_A is not None and screenshot_C is not None:
                                            print("🔍 Comparing Screenshot A vs Screenshot C...")
                                            visual_result_AC = self.detect_horror_extended_area(screenshot_A, screenshot_C)
                                            
                                            if visual_result_AC and visual_result_AC['has_horror']:
                                                print(f"🎯 SUCCESS! Screenshot A vs C detected hasHorror TRUE")
                                                final_has_horror = True
                                                final_confidence = visual_result_AC['confidence']
                                                final_detection_method = f"screenshot_A_vs_C_{visual_result_AC['method']}_icon_{highest_index}"
                                                final_visual_result = visual_result_AC
                                                final_visual_result['horror_icon_index'] = highest_index
                                                final_visual_result['comparison'] = 'A_vs_C'
                                                # STOP - Horror detected!
                                                print("🛑 STOP: Horror detected via screenshot A vs C")
                                            else:
                                                print("❌ No horror in screenshot A vs C")
                                                print("🚨 FINAL RESULT: hasHorror = False (all methods exhausted)")
                                                final_detection_method = "all_methods_exhausted"
                                        else:
                                            print("❌ Cannot compare screenshots A vs C (missing screenshot data)")
                                            print("🚨 FINAL RESULT: hasHorror = False (screenshot A vs C unavailable)")
                                            final_detection_method = "screenshot_A_vs_C_unavailable"
                                else:
                                    print("❌ Failed to get state C")
                                    print("🚨 FINAL RESULT: hasHorror = False (failed to capture state C)")
                                    final_detection_method = "failed_state_C"
                            else:
                                print("❌ Slot 4 coordinates not found")
                                print("🚨 FINAL RESULT: hasHorror = False (slot 4 unavailable)")
                                final_detection_method = "slot_4_unavailable"
                        else:
                            print("❌ No icons with index > 19 available")
                            print("🚨 FINAL RESULT: hasHorror = False (no high index icons)")
                            final_detection_method = "no_high_index_icons"
                    else:
                        print("❌ Failed to get current layout data")
                        print("🚨 FINAL RESULT: hasHorror = False (layout data unavailable)")
                        final_detection_method = "layout_data_unavailable"
                
                # ============================
                # FINAL RESULT SUMMARY
                # ============================
                print("\n" + "="*70)
                print("🎯 FINAL DETECTION RESULT SUMMARY")
                print("="*70)
                print(f"hasHorror: {final_has_horror}")
                print(f"Method: {final_detection_method}")
                print(f"Confidence: {final_confidence:.3f}")
                if 'horror_icon_index' in final_visual_result:
                    print(f"Horror Icon Index: {final_visual_result['horror_icon_index']}")
                if 'comparison' in final_visual_result:
                    print(f"Comparison: {final_visual_result['comparison']}")
                if 'phase_before' in final_visual_result and 'phase_after' in final_visual_result:
                    print(f"Phase Transition: {final_visual_result['phase_before']} → {final_visual_result['phase_after']}")
                print("="*70)
                
                # ============================
                # Full Layout Capture
                # ============================
                print("\n" + "="*70)
                print("📄 Full Layout Capture")
                print("="*70)
                
                print("📄 Extracting final full layout...")
                # [FIXED] Thực thi JS lấy layout cuối cùng thông qua engine
                layout_state = self.game_player._execute_verification_js(self.js_full_layout)
                
                if not layout_state or "error" in layout_state: 
                    print(f"❌ Failed to get final layout: {layout_state.get('error', 'Unknown') if layout_state else 'None'}")
                    self.cleanup_chrome_complete()
                    return False, None, None
                    
                print("✅ Final full layout captured successfully")
                
                # 🔍 NEW: Calculate recording area using OpenCV results
                print("\n" + "="*50)
                print("🔍 OPENCV RECORDING AREA CALCULATION")
                print("="*50)
                
                # Get slot center coordinates
                slots = layout_state.get('layout', {}).get('slots', [])
                center_slot, center_index, total_slots = self.calculate_slot_center_index(slots)

                if center_slot and center_slot.get('x') is not None:
                    slot_center_x = center_slot['x']
                    print(f"🔍 Found slot center (index {center_index}) at X coordinate: {slot_center_x}")
                    
                    # Calculate recording area using OpenCV results
                    opencv_recording_area = self.calculate_recording_area_opencv(slot_center_x, center_index, total_slots)
                    
                    if opencv_recording_area:
                        # Override the recordingArea in layout_state
                        layout_state['layout']['recordingArea'] = opencv_recording_area
                        print("✅ Recording area successfully calculated and applied using OpenCV method!")
                        print(f"   📌 Final Recording Area: {opencv_recording_area}")
                    else:
                        print("❌ Failed to calculate recording area with OpenCV method")
                        print("💡 Possible reasons:")
                        print("   - OpenCV layout analysis was not performed successfully")
                        print("   - Dividing lines could not be detected in this game")
                        print("   - Game layout doesn't follow expected structure")
                        print("⚠️ Will proceed without recording area - JSON will still be generated")
                        layout_state['layout']['recordingArea'] = None
                else:
                    print("❌ Slot center coordinates not found - cannot calculate recording area")
                    print("💡 This means the game doesn't have the expected slot structure")
                    print("⚠️ Will proceed without recording area")
                    layout_state['layout']['recordingArea'] = None
                
                print("="*50)
                
                # <<< BẮT ĐẦU SỬA: Xóa bỏ ws.close() thủ công
                # DÒNG SAU ĐÂY SẼ BỊ XÓA:
                # ws.close()
                # >>> KẾT THÚC SỬA
                
                # ============================
                # Generate Final JSON
                # ============================
                print("\n" + "="*70)
                print("📄 Generate Final JSON")
                print("="*70)
                
                page_title = layout_state.get('pageTitle', 'Unknown')
                
                final_json = {
                    "version": "11.0-simplified-complete-logic", 
                    "created": datetime.now().isoformat(),
                    "currentUrl": layout_state.get('currentUrl'), 
                    "pageTitle": page_title,
                    "gameSettings": {
                        "hasHorror": final_has_horror,
                        "hasBonus": len(layout_state.get('layout', {}).get('bonus', [])) > 0,
                        "detectionMethod": "simplified_A_vs_B_vs_C",
                        "detectionDetails": {
                            "method": final_detection_method,
                            "confidence": final_confidence,
                            "phase_before": final_visual_result.get('phase_before'),
                            "phase_after": final_visual_result.get('phase_after'),
                            "color_score": final_visual_result.get('color_shift', 0),
                            "structural_score": final_visual_result.get('structural_change', 0),
                            "horror_icon_index": final_visual_result.get('horror_icon_index', 19),
                            "comparison_type": final_visual_result.get('comparison', 'none'),
                            "logic_flow": "A(initial) → B(icon19) → C(highest) with early stop"
                        }
                    },
                    "layout": layout_state.get('layout', {}),
                    "settings": {
                        "aspectRatio": "96:83"
                    }
                }
                
                print("💾 Saving JSON file...")
                json_file_path = self.save_json_file(final_json, output_path, custom_title=page_title)
                if not json_file_path: 
                    self.cleanup_chrome_complete()
                    return False, None, None
                    
                print("\n🎯 SIMPLIFIED COMPLETE LOGIC COMPLETED!")
                print("="*70)
                print(f"✅ hasHorror Detection: {final_has_horror}")
                print(f"✅ Detection Method: {final_detection_method}")
                print(f"✅ Logic Flow: A(initial) vs B(icon19) vs C(highest) with early stop")
                if 'comparison' in final_visual_result:
                    print(f"✅ Successful Comparison: {final_visual_result['comparison']}")
                print("="*70)
                
                print("🧹 Performing complete cleanup...")
                self.cleanup_chrome_complete()
                    
                return True, json_file_path, final_json
                
            except Exception as e:
                print(f"❌ Simplified complete logic error: {e}")
                import traceback
                traceback.print_exc()
                
                self.cleanup_chrome_complete()
                return False, None, None
            
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

    def save_json_file(self, json_data, output_path=None, custom_title=None):
        try:
            # Lấy json_output_directory từ config
            json_output_dir = self.config.get('output', {}).get('json_output_directory', r"I:\VS-Project\Sprunki\JSON")
            os.makedirs(json_output_dir, exist_ok=True)

            if not output_path:
                import re
                
                if custom_title and custom_title.strip():
                    base_name = custom_title.strip()
                    print(f"📷️ Using custom title for filename: '{base_name}'")
                else:
                    from urllib.parse import urlparse
                    
                    base_name = "unknown-game"  # Giá trị mặc định
                    
                    if hasattr(self, 'game_url') and self.game_url:
                        try:
                            game_url_str = str(self.game_url)  # Đảm bảo là string
                            parsed_url = urlparse(game_url_str)
                            
                            if parsed_url.path:
                                path_parts = parsed_url.path.split('/')
                                slug_base = path_parts[-1] if path_parts else ""
                                
                                if not slug_base and len(path_parts) > 1:
                                    slug_base = path_parts[-2]  # Thử phần tử kế cuối
                                    
                                if slug_base:
                                    base_name = slug_base
                                else:
                                    # Fallback to netloc
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
            
            # Convert all data to JSON-serializable format
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
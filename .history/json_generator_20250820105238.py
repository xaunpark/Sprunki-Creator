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

# Try to import optional dependencies with fallbacks
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    print("Ã¢Å¡ Ã¯Â¸Â OpenCV not available - using basic image comparison")

try:
    from skimage.metrics import structural_similarity as ssim
    HAS_SSIM = True
except ImportError:
    HAS_SSIM = False
    print("Ã¢Å¡ Ã¯Â¸Â scikit-image not available - using custom SSIM implementation")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Ã¢Å¡ Ã¯Â¸Â PIL not available - using pyautogui screenshots only")

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
        self.user_data_dir = self.chrome_config.get('user_data_dir', 'chrome_debug_profile')
        
        # Game settings from config
        self.game_config = self.config.get('game', {})
        self.page_load_wait = self.game_config.get('page_load_wait', 20)
        self.game_load_wait = self.game_config.get('game_load_wait', 10)
        self.fullscreen_wait = self.game_config.get('fullscreen_wait', 8)
        self.max_pagedown_attempts = self.game_config.get('max_pagedown_attempts', 5)
        self.intro_skip_clicks = self.game_config.get('intro_skip_clicks', 10)
        
        print(f"âœ… JSON Generator configured from config.json:")
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
                // UPDATED: Hybrid Logic cho Icons (Logic MÃ¡Â»â€ºi)
                // =====================================================
                console.log("Ã°Å¸Å¡â‚¬ BÃ¡ÂºÂ¯t Ã„â€˜Ã¡ÂºÂ§u quÃƒÂ¡ trÃƒÂ¬nh lÃ¡ÂºÂ¥y icons (Logic Hybrid - ChÃƒÂ­nh xÃƒÂ¡c nhÃ¡ÂºÂ¥t)...");

                // BÃ†Â¯Ã¡Â»Å¡C 1: LÃ¡ÂºÂ¤Y CÃƒÂC ICON Ã„ÂANG HIÃ¡Â»â€šN THÃ¡Â»Å 
                console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 1: TÃƒÂ¬m cÃƒÂ¡c sprite 'Icons' Ã„â€˜ang hiÃ¡Â»Æ’n thÃ¡Â»â€¹...");
                const visibleIcons = allTargets.filter(t => t.sprite.name === 'Icons' && t.visible);
                console.log(`Ã¢Å“â€¦ TÃƒÂ¬m thÃ¡ÂºÂ¥y ${visibleIcons.length} icons trÃƒÂªn mÃƒ n hÃƒÂ¬nh.`);

                // BÃ†Â¯Ã¡Â»Å¡C 2: TÃ¡Âº O "DANH BÃ¡Âº " SPRITE (chÃ¡Â»â€° loÃ¡ÂºÂ¡i bÃ¡Â»Â Stage)
                console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 2: TÃ¡ÂºÂ¡o 'danh bÃ¡ÂºÂ¡' gÃ¡Â»â€œm tÃ¡ÂºÂ¥t cÃ¡ÂºÂ£ cÃƒÂ¡c sprite (trÃ¡Â»Â« Stage) Ã„â€˜Ã¡Â»Æ’ tra cÃ¡Â»Â©u...");
                const allSprites = allTargets.filter(t => !t.isStage);
                console.log(`Ã¢Å“â€¦ 'Danh bÃ¡ÂºÂ¡' cÃƒÂ³ ${allSprites.length} sprite.`);

                // BÃ†Â¯Ã¡Â»Å¡C 3: SÃ¡ÂºÂ®P XÃ¡ÂºÂ¾P ICONS DÃ¡Â»Â°A TRÃƒÅ N INDEX CÃ¡Â»Â¦A SPRITE TÃ†Â¯Ã† NG Ã¡Â»Â¨NG
                console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 3: SÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p icons bÃ¡ÂºÂ±ng cÃƒÂ¡ch liÃƒÂªn kÃ¡ÂºÂ¿t ngÃ†Â°Ã¡Â»Â£c lÃ¡ÂºÂ¡i vÃ¡Â»â€ºi sprite gÃ¡Â»â€˜c...");
                visibleIcons.sort((iconA, iconB) => {
                    // LÃ¡ÂºÂ¥y 'chÃƒÂ¬a khÃƒÂ³a' tÃ¡Â»Â« mÃ¡Â»â€”i icon
                    const charIndexA = Math.floor(iconA.currentCostume / 3);
                    const charIndexB = Math.floor(iconB.currentCostume / 3);

                    // DÃƒÂ¹ng 'chÃƒÂ¬a khÃƒÂ³a' Ã„â€˜Ã¡Â»Æ’ tra cÃ¡Â»Â©u trong 'danh bÃ¡ÂºÂ¡'
                    const linkedSpriteA = allSprites[charIndexA];
                    const linkedSpriteB = allSprites[charIndexB];

                    // NÃ¡ÂºÂ¿u vÃƒÂ¬ lÃƒÂ½ do nÃƒ o Ã„â€˜ÃƒÂ³ khÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y sprite, khÃƒÂ´ng sÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p
                    if (!linkedSpriteA || !linkedSpriteB) {
                        console.warn(`KhÃƒÂ´ng thÃ¡Â»Æ’ tÃƒÂ¬m thÃ¡ÂºÂ¥y sprite liÃƒÂªn kÃ¡ÂºÂ¿t cho index ${charIndexA} hoÃ¡ÂºÂ·c ${charIndexB}`);
                        return 0;
                    }

                    // LÃ¡ÂºÂ¥y 'sÃ¡Â»â€˜ bÃƒÂ¡o danh' (runtime index) cÃ¡Â»Â§a sprite Ã„â€˜ÃƒÂ£ liÃƒÂªn kÃ¡ÂºÂ¿t
                    const runtimeIndexA = allTargets.indexOf(linkedSpriteA);
                    const runtimeIndexB = allTargets.indexOf(linkedSpriteB);

                    // So sÃƒÂ¡nh 'sÃ¡Â»â€˜ bÃƒÂ¡o danh' Ã„â€˜Ã¡Â»Æ’ sÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p
                    return runtimeIndexA - runtimeIndexB;
                });

                // BÃ†Â¯Ã¡Â»Å¡C 4: TÃ¡Âº O MÃ¡ÂºÂ¢NG KÃ¡ÂºÂ¾T QUÃ¡ÂºÂ¢ CUÃ¡Â»ÂI CÃƒâ„¢NG
                console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 4: ChuyÃ¡Â»Æ’n Ã„â€˜Ã¡Â»â€¢i tÃ¡Â»Âa Ã„â€˜Ã¡Â»â„¢ vÃƒ  gÃƒÂ¡n index tuÃ¡ÂºÂ§n tÃ¡Â»Â± mÃ¡Â»â€ºi...");
                const icons = visibleIcons.map((icon, index) => {
                    const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                    return {
                        x: pixelCoords.x,
                        y: pixelCoords.y,
                        index: index // GÃƒÂ¡n index tuÃ¡ÂºÂ§n tÃ¡Â»Â± 0, 1, 2... sau khi Ã„â€˜ÃƒÂ£ sÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p
                    };
                });

                console.log("Ã¢Å“â€¦ HOÃƒâ‚¬N THÃƒâ‚¬NH! Icons Ã„â€˜Ã†Â°Ã¡Â»Â£c tÃ¡ÂºÂ¡o theo logic Hybrid mÃ¡Â»â€ºi:");
                console.table(icons);
                // =====================================================
                // END: UPDATED Hybrid Logic cho Icons
                // =====================================================
                
                // Get slots coordinates
                const poloSprites = allTargets.filter(t => t.sprite.name.startsWith('Polo '));
                poloSprites.sort((a, b) => (parseInt(a.sprite.name.match(/\\d+/)?.[0] || '0') - parseInt(b.sprite.name.match(/\\d+/)?.[0] || '0')));
                const slots = poloSprites.map((s, i) => ({ ...toPixelWithMenu(s.x, s.y), index: i }));
                
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
                console.log("Ã°Å¸â€Â Searching for ALL Skip Intro buttons (multiple detection)...");
                
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
                    console.log("Ã¢ÂÅ’ VM not found - cannot search for Skip Intro buttons");
                    resolve({ found: false, count: 0, buttons: [], reason: "vm_not_found" });
                    return;
                }
                
                const allTargets = vm.runtime.targets.map(t => t);
                console.log(`Ã°Å¸Å½Â¯ Total targets found: ${allTargets.length}`);
                
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
                
                console.log(`Ã°Å¸â€Â Found ${introTargets.length} 'Intro' targets with 'Skip' costume`);
                
                if (introTargets.length === 0) {
                    console.log("Ã¢â€žÂ¹Ã¯Â¸Â No Skip Intro buttons found");
                    
                    // DEBUG: Check for alternative patterns
                    const skipCostumeTargets = allTargets.filter(target => 
                        target.sprite.costumes.some(costume => costume.name === "Skip")
                    );
                    
                    if (skipCostumeTargets.length > 0) {
                        console.log(`Ã°Å¸â€Â Found ${skipCostumeTargets.length} targets with 'Skip' costume but different names:`);
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
                    console.log(`\\nÃ°Å¸Å½Â¯ Processing Intro Target ${index + 1}/${introTargets.length}:`);
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
                        
                        console.log(`   Ã¢Å“â€¦ Converted to pixel coordinates: (${pixelCoords.x}, ${pixelCoords.y})`);
                        console.log(`   Ã°Å¸â€œâ€¹ Added to click list (${target.visible ? 'VISIBLE' : 'HIDDEN'})`);
                    } else {
                        console.log(`   Ã¢ÂÅ’ Coordinate conversion failed for target ${index + 1}`);
                    }
                });
                
                // Summary
                const visibleButtons = skipIntroButtons.filter(btn => btn.visible);
                const hiddenButtons = skipIntroButtons.filter(btn => !btn.visible);
                
                console.log(`\\nÃ°Å¸â€œÅ  SKIP INTRO BUTTONS SUMMARY:`);
                console.log(`   - Total found: ${skipIntroButtons.length}`);
                console.log(`   - Visible: ${visibleButtons.length}`);
                console.log(`   - Hidden: ${hiddenButtons.length}`);
                
                if (skipIntroButtons.length > 0) {
                    console.log(`\\nÃ°Å¸Å½Â¯ Will click ALL ${skipIntroButtons.length} buttons to ensure success:`);
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
                console.error("Ã¢ÂÅ’ Error searching for Skip Intro buttons:", e);
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
                
                function calculateRecordingArea(slots, allTargets) {
                    const stageW = 640; const stageH = 360; const screenW = 1920; const screenH = 1080; const menuWidth = 78;
                    const viewportW = screenW - menuWidth; const scale = Math.min(viewportW / stageW, screenH / stageH);
                    const slot3 = slots.find(s => s.index === 3);
                    if (!slot3) return null;
                    const targetSprite = allTargets.find(t => {
                        const nameMatch = t.sprite.name.match(/Polo (\d+)/);
                        return nameMatch && parseInt(nameMatch[1], 10) === (slot3.index + 1);
                    });
                    if (!targetSprite || !targetSprite.sprite.costumes.length) return null;
                    const targetCostume = targetSprite.sprite.costumes[targetSprite.currentCostume];
                    if (!targetCostume || !targetCostume.size) return null;
                    const centerY = slot3.y; const spriteSizeRatio = targetSprite.size / 100;
                    const bottomOffsetInCostume = targetCostume.size[1] - targetCostume.rotationCenterY;
                    const charBottomOffset = bottomOffsetInCostume * scale * spriteSizeRatio;
                    const bottomBoundaryY = (centerY + charBottomOffset) - 50;
                    const actualWidthOfChar = targetCostume.size[0] * scale * spriteSizeRatio;
                    const finalWidth = Math.round(actualWidthOfChar * 3);
                    const finalHeight = Math.round(finalWidth * (83 / 96));
                    const centerX = slot3.x; const finalRecX = Math.round(centerX - (finalWidth / 2));
                    let finalRecY = Math.round(bottomBoundaryY - finalHeight);
                    if (finalRecY < 0) { finalRecY = 0; }
                    return { x: finalRecX, y: finalRecY, width: finalWidth, height: finalHeight, aspectRatio: "96:83" };
                }

                // =====================================================
                // UPDATED: Function getIconsWithHybridLogic (Logic MÃ¡Â»â€ºi)
                // =====================================================
                function getIconsWithHybridLogic(allTargets) {
                    console.log("Ã°Å¸Å¡â‚¬ BÃ¡ÂºÂ¯t Ã„â€˜Ã¡ÂºÂ§u quÃƒÂ¡ trÃƒÂ¬nh lÃ¡ÂºÂ¥y icons (Logic Hybrid - ChÃƒÂ­nh xÃƒÂ¡c nhÃ¡ÂºÂ¥t)...");

                    // BÃ†Â¯Ã¡Â»Å¡C 1: LÃ¡ÂºÂ¤Y CÃƒÂC ICON Ã„ÂANG HIÃ¡Â»â€šN THÃ¡Â»Å 
                    console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 1: TÃƒÂ¬m cÃƒÂ¡c sprite 'Icons' Ã„â€˜ang hiÃ¡Â»Æ’n thÃ¡Â»â€¹...");
                    const visibleIcons = allTargets.filter(t => t.sprite.name === 'Icons' && t.visible);
                    console.log(`Ã¢Å“â€¦ TÃƒÂ¬m thÃ¡ÂºÂ¥y ${visibleIcons.length} icons trÃƒÂªn mÃƒ n hÃƒÂ¬nh.`);

                    // BÃ†Â¯Ã¡Â»Å¡C 2: TÃ¡Âº O "DANH BÃ¡Âº " SPRITE (chÃ¡Â»â€° loÃ¡ÂºÂ¡i bÃ¡Â»Â Stage)
                    console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 2: TÃ¡ÂºÂ¡o 'danh bÃ¡ÂºÂ¡' gÃ¡Â»â€œm tÃ¡ÂºÂ¥t cÃ¡ÂºÂ£ cÃƒÂ¡c sprite (trÃ¡Â»Â« Stage) Ã„â€˜Ã¡Â»Æ’ tra cÃ¡Â»Â©u...");
                    const allSprites = allTargets.filter(t => !t.isStage);
                    console.log(`Ã¢Å“â€¦ 'Danh bÃ¡ÂºÂ¡' cÃƒÂ³ ${allSprites.length} sprite.`);

                    // BÃ†Â¯Ã¡Â»Å¡C 3: SÃ¡ÂºÂ®P XÃ¡ÂºÂ¾P ICONS DÃ¡Â»Â°A TRÃƒÅ N INDEX CÃ¡Â»Â¦A SPRITE TÃ†Â¯Ã† NG Ã¡Â»Â¨NG
                    console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 3: SÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p icons bÃ¡ÂºÂ±ng cÃƒÂ¡ch liÃƒÂªn kÃ¡ÂºÂ¿t ngÃ†Â°Ã¡Â»Â£c lÃ¡ÂºÂ¡i vÃ¡Â»â€ºi sprite gÃ¡Â»â€˜c...");
                    visibleIcons.sort((iconA, iconB) => {
                        // LÃ¡ÂºÂ¥y 'chÃƒÂ¬a khÃƒÂ³a' tÃ¡Â»Â« mÃ¡Â»â€”i icon
                        const charIndexA = Math.floor(iconA.currentCostume / 3);
                        const charIndexB = Math.floor(iconB.currentCostume / 3);

                        // DÃƒÂ¹ng 'chÃƒÂ¬a khÃƒÂ³a' Ã„â€˜Ã¡Â»Æ’ tra cÃ¡Â»Â©u trong 'danh bÃ¡ÂºÂ¡'
                        const linkedSpriteA = allSprites[charIndexA];
                        const linkedSpriteB = allSprites[charIndexB];

                        // NÃ¡ÂºÂ¿u vÃƒÂ¬ lÃƒÂ½ do nÃƒ o Ã„â€˜ÃƒÂ³ khÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y sprite, khÃƒÂ´ng sÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p
                        if (!linkedSpriteA || !linkedSpriteB) {
                            console.warn(`KhÃƒÂ´ng thÃ¡Â»Æ’ tÃƒÂ¬m thÃ¡ÂºÂ¥y sprite liÃƒÂªn kÃ¡ÂºÂ¿t cho index ${charIndexA} hoÃ¡ÂºÂ·c ${charIndexB}`);
                            return 0;
                        }

                        // LÃ¡ÂºÂ¥y 'sÃ¡Â»â€˜ bÃƒÂ¡o danh' (runtime index) cÃ¡Â»Â§a sprite Ã„â€˜ÃƒÂ£ liÃƒÂªn kÃ¡ÂºÂ¿t
                        const runtimeIndexA = allTargets.indexOf(linkedSpriteA);
                        const runtimeIndexB = allTargets.indexOf(linkedSpriteB);

                        // So sÃƒÂ¡nh 'sÃ¡Â»â€˜ bÃƒÂ¡o danh' Ã„â€˜Ã¡Â»Æ’ sÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p
                        return runtimeIndexA - runtimeIndexB;
                    });

                    // BÃ†Â¯Ã¡Â»Å¡C 4: TÃ¡Âº O MÃ¡ÂºÂ¢NG KÃ¡ÂºÂ¾T QUÃ¡ÂºÂ¢ CUÃ¡Â»ÂI CÃƒâ„¢NG
                    console.log("Ã¢â€“Â¶Ã¯Â¸Â BÃ†Â°Ã¡Â»â€ºc 4: ChuyÃ¡Â»Æ’n Ã„â€˜Ã¡Â»â€¢i tÃ¡Â»Âa Ã„â€˜Ã¡Â»â„¢ vÃƒ  gÃƒÂ¡n index tuÃ¡ÂºÂ§n tÃ¡Â»Â± mÃ¡Â»â€ºi...");
                    const finalLayoutIcons = visibleIcons.map((icon, index) => {
                        const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                        return {
                            x: pixelCoords.x,
                            y: pixelCoords.y,
                            index: index // GÃƒÂ¡n index tuÃ¡ÂºÂ§n tÃ¡Â»Â± 0, 1, 2... sau khi Ã„â€˜ÃƒÂ£ sÃ¡ÂºÂ¯p xÃ¡ÂºÂ¿p
                        };
                    });

                    console.log("Ã¢Å“â€¦ HOÃƒâ‚¬N THÃƒâ‚¬NH! Icons Ã„â€˜Ã†Â°Ã¡Â»Â£c tÃ¡ÂºÂ¡o theo logic Hybrid mÃ¡Â»â€ºi:");
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
                
                const poloSprites = allTargets.filter(t => /^Polo \d+$/.test(t.sprite.name));
                poloSprites.sort((a, b) => {
                    const numA = parseInt(a.sprite.name.match(/\d+/)?.[0] || '0');
                    const numB = parseInt(b.sprite.name.match(/\d+/)?.[0] || '0');
                    return numA - numB;
                });
                const slots = poloSprites.map((s, i) => {
                    const coords = toPixelWithMenu(s.x, s.y);
                    return { ...coords, index: i };
                });
                
                const customRecordingArea = calculateRecordingArea(slots, allTargets);
                
                // =====================================================
                // NEW: Find Skip Intro Button Logic
                // =====================================================
                function findSkipIntroButton(allTargets) {
                    console.log("Ã°Å¸â€Â Searching for Skip Intro button...");
                    
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
                                console.log(`Ã¢Å“â€¦ Found Skip Intro target: ${target.sprite.name}`);
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
                                console.log(`Ã¢Å“â€¦ Skip Intro button found at pixel coordinates: (${pixelCoords.x}, ${pixelCoords.y})`);
                                return {
                                    x: pixelCoords.x,
                                    y: pixelCoords.y,
                                    visible: introTarget.visible,
                                    targetName: introTarget.sprite.name,
                                    currentCostume: introTarget.currentCostume,
                                    costumes: introTarget.sprite.costumes.map(c => c.name)
                                };
                            } else {
                                console.warn("Ã¢Å¡ Ã¯Â¸Â Skip Intro target found but coordinates conversion failed");
                                return null;
                            }
                        } else {
                            console.log("Ã¢â€žÂ¹Ã¯Â¸Â Skip Intro button not found (no target with name 'Intro' and costume 'Skip')");
                            return null;
                        }
                        
                    } catch (error) {
                        console.error("Ã¢ÂÅ’ Error while searching for Skip Intro button:", error);
                        return null;
                    }
                }

                // Find Skip Intro button
                const skipIntroButton = findSkipIntroButton(allTargets);

                // Log the result for debugging
                if (skipIntroButton) {
                    console.log("Ã°Å¸Å½Â¯ Skip Intro Button Detection Result:");
                    console.log(`   - Found: YES`);
                    console.log(`   - Coordinates: (${skipIntroButton.x}, ${skipIntroButton.y})`);
                    console.log(`   - Visible: ${skipIntroButton.visible}`);
                    console.log(`   - Target Name: ${skipIntroButton.targetName}`);
                    console.log(`   - Available Costumes: ${skipIntroButton.costumes.join(', ')}`);
                } else {
                    console.log("Ã°Å¸Å½Â¯ Skip Intro Button Detection Result:");
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
                        recordingArea: customRecordingArea,
                        skipIntroButton: skipIntroButton  // NEW: Add Skip Intro button coordinates
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
                
                print(f"âœ… JSON Generator verification config loaded:")
                print(f"   Page verification: {'âœ… Enabled' if self.page_verification_enabled else 'âŒ Disabled'}")
                print(f"   Game verification: {'âœ… Enabled' if self.game_verification_enabled else 'âŒ Disabled'}")
                print(f"   Page timeout: {self.page_verification_timeout}s")
                print(f"   Game timeout: {self.game_verification_timeout}s")
                print(f"   Stabilization wait: {self.stabilization_wait}s")
                
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

    def _execute_verification_js(self, js_code):
        """Execute JavaScript for verification via WebSocket/CDP"""
        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                print("âš ï¸ WebSocket not available for verification, skipping JS check")
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_code, "returnByValue": True}
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

    def verify_page_fully_loaded(self, target_url):
        """
        ðŸ” Smart Page Load Verification System for JSON Generator
        Checks if page is fully loaded before proceeding with Run button detection
        """
        try:
            if not self.page_verification_enabled:
                print("ðŸ” Page verification disabled, assuming page ready")
                return True
            
            print(f"\nðŸ” JSON GENERATOR - SMART PAGE VERIFICATION: {target_url}")
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
                print(f"ðŸ” JSON Generator page verification attempt {attempt}/{max_retries}")
                
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
                        print(f"âœ… JSON GENERATOR PAGE VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
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
                        return True
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.page_verification_timeout:
                    print(f"âš ï¸ JSON Generator page verification timeout ({self.page_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with Run button detection...")
                    return True  # Don't fail completely, proceed with detection
                
                # Wait before next attempt
                if attempt < max_retries:
                    print(f"   â³ Waiting {self.page_verification_interval}s before next check...")
                    time.sleep(self.page_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"âš ï¸ JSON Generator page verification failed after {max_retries} attempts")
            print("   Proceeding anyway with Run button detection...")
            return True
            
        except Exception as e:
            print(f"âš ï¸ Error in JSON Generator page verification: {e}")
            print("   Proceeding anyway with Run button detection...")
            return True  # Don't block workflow on verification errors

    def verify_game_components_ready(self):
        """
        ðŸŽ® Smart Game Load Verification System for JSON Generator
        Checks if game components are ready before proceeding with Run button detection
        """
        try:
            if not self.game_verification_enabled:
                print("ðŸŽ® Game verification disabled, assuming game ready")
                return True
            
            print(f"\nðŸŽ® JSON GENERATOR - SMART GAME VERIFICATION")
            print("="*70)
            
            # JavaScript for game component verification
            game_verification_js = f"""
            (() => {{
                const selectors = {self.game_container_selectors};
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
                
                // Check game container elements
                selectors.forEach(selector => {{
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0) {{
                        elements.forEach((el, index) => {{
                            const rect = el.getBoundingClientRect();
                            results.gameContainers.push({{
                                selector: selector,
                                index: index,
                                id: el.id || 'no-id',
                                className: el.className || 'no-class',
                                visible: rect.width > 0 && rect.height > 0,
                                hasChildren: el.children.length > 0
                            }});
                        }});
                    }}
                }});
                
                // Check for React components
                const checkReactComponents = () => {{
                    const elements = document.querySelectorAll('*');
                    let reactFound = false;
                    for (let el of elements) {{
                        const keys = Object.keys(el);
                        const reactKey = keys.find(key => key.startsWith('__react'));
                        if (reactKey) {{
                            reactFound = true;
                            results.reactComponents.details.push({{
                                element: el.tagName,
                                reactKey: reactKey.substring(0, 20) + '...'
                            }});
                            if (results.reactComponents.details.length >= 3) break;
                        }}
                    }}
                    results.reactComponents.found = reactFound;
                }};
                
                if ({str(self.check_react_components).lower()}) {{
                    checkReactComponents();
                }}
                
                // Check if Run button exists in DOM
                if ({str(self.check_run_button_dom).lower()}) {{
                    const runButtonSelectors = [
                        'button[type="submit"]',
                        'button:contains("Run")',
                        '.run-button',
                        '#run-button',
                        '[class*="run"]',
                        '[class*="play"]',
                        '[class*="start"]',
                        'button',
                        'input[type="submit"]'
                    ];
                    
                    for (let selector of runButtonSelectors) {{
                        try {{
                            const buttons = document.querySelectorAll(selector);
                            for (let button of buttons) {{
                                const text = button.textContent || button.value || button.alt || '';
                                if (text.toLowerCase().includes('run') || 
                                    text.toLowerCase().includes('play') || 
                                    text.toLowerCase().includes('start')) {{
                                    results.runButtonInDOM = true;
                                    break;
                                }}
                            }}
                            if (results.runButtonInDOM) break;
                        }} catch (e) {{
                            // Skip invalid selectors
                        }}
                    }}
                }}
                
                // Assessment
                const gameContainersFound = results.gameContainers.length >= {self.min_containers_required};
                const reactReady = !{str(self.check_react_components).lower()} || results.reactComponents.found;
                const runButtonReady = !{str(self.check_run_button_dom).lower()} || results.runButtonInDOM;
                
                results.overallReady = gameContainersFound && reactReady && runButtonReady;
                
                return results;
            }})();
            """
            
            start_time = time.time()
            max_attempts = 3
            
            for attempt in range(1, max_attempts + 1):
                print(f"ðŸŽ® JSON Generator game verification attempt {attempt}/{max_attempts}")
                
                # Execute game verification JavaScript
                game_result = self._execute_verification_js(game_verification_js)
                
                if game_result:
                    game_containers = game_result.get('gameContainers', [])
                    react_components = game_result.get('reactComponents', {})
                    run_button_in_dom = game_result.get('runButtonInDOM', False)
                    overall_ready = game_result.get('overallReady', False)
                    
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
                        print(f"âœ… JSON GENERATOR GAME VERIFICATION SUCCESSFUL! (took {elapsed:.1f}s)")
                        print("="*70)
                        return True
                    else:
                        if len(game_containers) < self.min_containers_required:
                            print(f"   â³ Waiting for game containers to load ({len(game_containers)}/{self.min_containers_required})")
                        if self.check_react_components and not react_components.get('found'):
                            print(f"   â³ Waiting for React components to initialize")
                        if self.check_run_button_dom and not run_button_in_dom:
                            print(f"   â³ Waiting for Run button to appear in DOM")
                
                else:
                    print(f"   âš ï¸ Game verification JavaScript failed")
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.game_verification_timeout:
                    print(f"âš ï¸ JSON Generator game verification timeout ({self.game_verification_timeout}s exceeded)")
                    print("   Proceeding anyway with Run button detection...")
                    return True
                
                # Wait before next attempt
                if attempt < max_attempts:
                    print(f"   â³ Waiting {self.game_verification_interval}s before next check...")
                    time.sleep(self.game_verification_interval)
            
            # All attempts failed, but don't block the workflow
            print(f"âš ï¸ JSON Generator game verification failed after {max_attempts} attempts")
            print("   Proceeding anyway with Run button detection...")
            return True
            
        except Exception as e:
            print(f"âš ï¸ Error in JSON Generator game verification: {e}")
            print("   Proceeding anyway with Run button detection...")
            return True

    def find_phase_variable(self, state_data):
        """Find phase variable in state data"""
        try:
            print("Ã°Å¸â€Â Searching for phase variable in state data...")
            
            # Search in global variables first
            variables = state_data.get('variables', {})
            
            # Look for phase variable by name
            for var_key, var_data in variables.items():
                if isinstance(var_data, dict) and var_data.get('name') == 'phase':
                    print(f"Ã¢Å“â€¦ Found phase variable in global variables: {var_data}")
                    return var_data.get('value')
            
            # Search in allTargets variables
            all_targets = state_data.get('allTargets', [])
            for target in all_targets:
                target_vars = target.get('variables', {})
                for var_key, var_data in target_vars.items():
                    if isinstance(var_data, dict) and var_data.get('name') == 'phase':
                        print(f"Ã¢Å“â€¦ Found phase variable in target '{target.get('name', 'Unknown')}': {var_data}")
                        return var_data.get('value')
            
            print("Ã¢Å¡ Ã¯Â¸Â Phase variable not found in state data")
            return None
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Error finding phase variable: {e}")
            return None

    def detect_horror_by_phase_comparison(self, before_state, after_state):
        """Detect horror by comparing phase value before and after drag"""
        try:
            print("\nÃ°Å¸â€Â PHASE COMPARISON HORROR DETECTION")
            print("="*60)
            
            # Find phase values
            phase_before = self.find_phase_variable(before_state)
            phase_after = self.find_phase_variable(after_state)
            
            print(f"Ã°Å¸â€œÅ  Phase value BEFORE drag: {phase_before}")
            print(f"Ã°Å¸â€œÅ  Phase value AFTER drag: {phase_after}")
            
            # Convert to string for comparison (handle both string and numeric values)
            phase_before_str = str(phase_before) if phase_before is not None else None
            phase_after_str = str(phase_after) if phase_after is not None else None
            
            if phase_before_str is None or phase_after_str is None:
                print("Ã¢ÂÅ’ Cannot find phase variable - will use screenshot fallback")
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
            
            print(f"\nÃ°Å¸Å½Â¯ PHASE COMPARISON RESULT:")
            print(f"   Phase transition: {phase_before_str} -> {phase_after_str}")
            print(f"   Has Horror: {has_horror} (only 1->2 counts as horror)")
            print(f"   Method: {method}")
            print(f"   Confidence: {confidence:.1f}")
            print("="*60)
            
            return result
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Error in phase comparison: {e}")
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
            print("Ã°Å¸â€œÂ Calculating icons area from state data...")
            
            # Check if state_data has icons directly or needs to be extracted
            icons = None
            if 'icons' in state_data:
                icons = state_data['icons']
            elif 'layout' in state_data and 'icons' in state_data['layout']:
                icons = state_data['layout']['icons']
            else:
                print("Ã¢ÂÅ’ No icons data found in state")
                return None
            
            if not icons:
                print("Ã¢ÂÅ’ Icons list is empty")
                return None
                
            # Filter icons 0-18 (exclude icon 19 if exists)
            target_icons = [icon for icon in icons if icon.get('index', 0) <= 18]
            
            if len(target_icons) < 5:
                print(f"Ã¢ÂÅ’ Only {len(target_icons)} target icons available, need at least 5")
                return None
            
            # Find valid coordinates
            valid_icons = [icon for icon in target_icons if icon.get('x') is not None and icon.get('y') is not None]
            
            if len(valid_icons) == 0:
                print("Ã¢ÂÅ’ No icons with valid coordinates found")
                return None
                
            print(f"Ã°Å¸â€œÅ  Found {len(valid_icons)} icons with valid coordinates (0-18)")
            
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
            
            print(f"Ã°Å¸â€œÂ Icons area (0-18): {area['x']}, {area['y']}, {area['width']}x{area['height']}")
            return area
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Error calculating icons area: {e}")
            return None

    def capture_icons_area_screenshot(self, area_coords):
        """Capture screenshot of icons 0-18 area"""
        try:
            if not area_coords:
                return None
                
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
            print(f"Ã°Å¸â€œÂ¸ Screenshot captured: {screenshot_np.shape}")
            return screenshot_np
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Error capturing screenshot: {e}")
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
            print(f"Ã¢ÂÅ’ Error calculating histogram: {e}")
            return {'red': 0, 'green': 0, 'blue': 0, 'brightness': 0, 'saturation': 0}

    def compare_icons_histogram(self, before_img, after_img):
        """Histogram comparison for horror detection"""
        try:
            print("Ã°Å¸Å½Â¨ Analyzing color histogram changes...")
            
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
            print(f"Ã¢ÂÅ’ Error in histogram comparison: {e}")
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
            print("Ã°Å¸â€Â Analyzing structural similarity (SSIM)...")
            
            # Convert to grayscale for structure comparison
            before_gray = self.rgb_to_grayscale(before_img)
            after_gray = self.rgb_to_grayscale(after_img)
            
            # Ensure same dimensions
            if before_gray.shape != after_gray.shape:
                min_h = min(before_gray.shape[0], after_gray.shape[0])
                min_w = min(before_gray.shape[1], after_gray.shape[1])
                before_gray = before_gray[:min_h, :min_w]
                after_gray = after_gray[:min_h, :min_w]
            
            # Calculate SSIM
            if HAS_SSIM:
                similarity_score = ssim(before_gray, after_gray)
            else:
                # Fallback simple SSIM
                similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
            
            difference_score = 1 - similarity_score
            
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
            print(f"Ã¢ÂÅ’ Error in SSIM comparison: {e}")
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
            print(f"Ã¢ÂÅ’ Error in simple SSIM: {e}")
            return 0.5

    def detect_horror_icons_area(self, before_img, after_img):
        """Histogram + SSIM Combo for horror detection"""
        try:
            print("\nÃ°Å¸â€Â VISUAL HORROR DETECTION - Histogram + SSIM Combo")
            print("="*60)
            
            if before_img is None or after_img is None:
                print("Ã¢ÂÅ’ Missing screenshot data")
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
            
            print(f"\nÃ°Å¸Å½Â¯ HISTOGRAM + SSIM COMBO RESULT:")
            print(f"   Color Horror Score: {color_result['horror_score']:.3f} (threshold: 0.25)")
            print(f"   Structural Change: {structural_result['difference_score']:.3f} (threshold: 0.30)")  
            print(f"   Has Horror: {has_horror} (method: {method})")
            print(f"   Confidence: {confidence:.3f}")
            print("="*60)
            
            return result
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Error in horror detection: {e}")
            import traceback
            traceback.print_exc()
            return {'has_horror': False, 'confidence': 0, 'method': 'error', 'reason': str(e)}

    def calculate_extended_screenshot_area(self, state_data):
        """Calculate extended area from (0,0) to icon index 18 coordinates"""
        try:
            print("Ã°Å¸â€œÂ Calculating extended screenshot area from (0,0) to icon 18...")
            
            # Check if state_data has icons directly or needs to be extracted
            icons = None
            if 'icons' in state_data:
                icons = state_data['icons']
            elif 'layout' in state_data and 'icons' in state_data['layout']:
                icons = state_data['layout']['icons']
            else:
                print("Ã¢ÂÅ’ No icons data found in state")
                return None
            
            if not icons:
                print("Ã¢ÂÅ’ Icons list is empty")
                return None
                
            # Find icon index 18 (highest icon before icon 19)
            icon_18 = None
            for icon in icons:
                if icon.get('index') == 18:
                    icon_18 = icon
                    break
            
            if not icon_18 or icon_18.get('x') is None or icon_18.get('y') is None:
                print("Ã¢ÂÅ’ Icon index 18 not found or has invalid coordinates")
                return None
            
            print(f"Ã°Å¸â€œÅ  Found icon 18 at coordinates: ({icon_18['x']}, {icon_18['y']})")
            
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
            
            print(f"Ã°Å¸â€œÂ Extended screenshot area: {area['x']}, {area['y']}, {area['width']}x{area['height']}")
            print(f"   Coverage: (0,0) to icon 18 at ({icon_18['x']}, {icon_18['y']})")
            return area
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Error calculating extended screenshot area: {e}")
            return None

    def compare_icons_histogram_extended(self, before_img, after_img):
        """Enhanced histogram comparison for extended area with dynamic background tolerance"""
        try:
            print("Ã°Å¸Å½Â¨ Analyzing color histogram changes (Extended Area)...")
            
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
            # Original was 0.25, now 0.40 to account for background movement
            threshold = 0.40
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
            print(f"Ã¢ÂÅ’ Error in extended histogram comparison: {e}")
            return {'has_horror': False, 'horror_score': 0, 'red_shift': 0, 'brightness_change': 0, 'saturation_change': 0, 'threshold_used': 0.40}

    def compare_icons_ssim_extended(self, before_img, after_img):
        """Enhanced SSIM comparison for extended area with dynamic background tolerance"""
        try:
            print("Ã°Å¸â€Â Analyzing structural similarity (SSIM - Extended Area)...")
            
            # Convert to grayscale for structure comparison
            before_gray = self.rgb_to_grayscale(before_img)
            after_gray = self.rgb_to_grayscale(after_img)
            
            # Ensure same dimensions
            if before_gray.shape != after_gray.shape:
                min_h = min(before_gray.shape[0], after_gray.shape[0])
                min_w = min(before_gray.shape[1], after_gray.shape[1])
                before_gray = before_gray[:min_h, :min_w]
                after_gray = after_gray[:min_h, :min_w]
            
            # Calculate SSIM
            if HAS_SSIM:
                similarity_score = ssim(before_gray, after_gray)
            else:
                # Fallback simple SSIM
                similarity_score = self.simple_ssim_fallback(before_gray, after_gray)
            
            difference_score = 1 - similarity_score
            
            # INCREASED threshold for extended area with dynamic background
            # Original was 0.30, now 0.45 to account for background movement
            threshold = 0.45
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
            print(f"Ã¢ÂÅ’ Error in extended SSIM comparison: {e}")
            return {'has_horror': False, 'similarity_score': 1.0, 'difference_score': 0.0, 'threshold_used': 0.45}

    def detect_horror_extended_area(self, before_img, after_img):
        """Enhanced horror detection for extended area (0,0) to icon 18 with background tolerance"""
        try:
            print("\nÃ°Å¸â€Â EXTENDED AREA HORROR DETECTION - Enhanced for Dynamic Background")
            print("="*70)
            print("Coverage: (0,0) to icon 18 - Adjusted thresholds for background movement")
            print("="*70)
            
            if before_img is None or after_img is None:
                print("Ã¢ÂÅ’ Missing screenshot data")
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
            
            print(f"\nÃ°Å¸Å½Â¯ EXTENDED AREA DETECTION RESULT:")
            print(f"   Color Horror Score: {color_result['horror_score']:.3f} (threshold: {color_result['threshold_used']})")
            print(f"   Structural Change: {structural_result['difference_score']:.3f} (threshold: {structural_result['threshold_used']})")  
            print(f"   Has Horror: {has_horror} (method: {method})")
            print(f"   High Confidence: {high_confidence_horror}")
            print(f"   Final Confidence: {confidence:.3f}")
            print(f"   Area Coverage: (0,0) to icon 18 position")
            print("="*70)
            
            return result
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Error in extended area horror detection: {e}")
            import traceback
            traceback.print_exc()
            return {'has_horror': False, 'confidence': 0, 'method': 'error', 'reason': str(e), 'area_type': 'extended_0_0_to_icon18'}

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
                except websocket.WebSocketTimeoutError:
                    print("WARNING: WebSocket timeout, continuing to wait...")
                    continue
                    
            print(f"ERROR: CDP command timeout after {max_wait_time}s")
            return None
            
        except Exception as e:
            print(f"ERROR: CDP command error: {e}")
            return None

    def _simulate_click_via_cdp(self, ws, x, y):
        down_command = {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}
        self._send_cdp_command(ws, down_command)
        time.sleep(random.uniform(0.05, 0.1))
        up_command = {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}
        self._send_cdp_command(ws, up_command)

    def _simulate_drag_via_cdp(self, ws, start_x, start_y, end_x, end_y):
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
        time.sleep(0.2)
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1}})
        time.sleep(0.2)
        
        steps = 20
        for i in range(1, steps + 1):
            progress = i / steps
            curr_x = start_x + (end_x - start_x) * progress
            curr_y = start_y + (end_y - start_y) * progress
            self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left"}})
            time.sleep(0.02)
            
        self._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1}})

    def cleanup_chrome_complete(self):
        """Complete cleanup: Close Chrome debug entirely and clean all resources"""
        try:
            print("Ã°Å¸Â§Â¹ Complete cleanup: Closing Chrome debug and cleaning resources...")
            
            for title_part in ['cocrea.world', 'Chrome']:
                windows = gw.getWindowsWithTitle(title_part)
                for window in windows:
                    try:
                        print(f"   Ã°Å¸â€”â€šÃ¯Â¸Â Closing window: {window.title}")
                        window.close()
                    except:
                        pass
            
            time.sleep(2)
            
            if hasattr(self, 'chrome_process') and self.chrome_process:
                try:
                    print("   Ã°Å¸â€â€ž Terminating Chrome process...")
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
                                print(f"   Ã¢Å¡â€Ã¯Â¸Â Killing leftover Chrome debug process: {proc.info['pid']}")
                                proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except ImportError:
                print("   Ã¢Å¡ Ã¯Â¸Â psutil not available for process cleanup")
            
            self.ws_url = None
            
            print("Ã¢Å“â€¦ Complete cleanup finished - Chrome debug fully closed")
            time.sleep(1)
            return True
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Complete cleanup error: {e}")
            return False

    def navigate_and_prepare_game(self, url):
            """Navigate to game URL using Chrome debug mode with Smart Verification for JSON Generator"""
            try:
                print(f"ðŸŒ JSON Generator: Opening game URL with fresh Chrome debug session: {url}")
                
                self.cleanup_chrome_complete()
                time.sleep(2)
                
                self.game_url = url
                
                extensions_dir = os.path.join(os.getcwd(), 'extensions')
                ublock_path = None
                if os.path.exists(extensions_dir):
                    subdirs = [d for d in os.listdir(extensions_dir) if os.path.isdir(os.path.join(extensions_dir, d))]
                    if subdirs: 
                        ublock_path = os.path.join(extensions_dir, subdirs[0])
                
                if not ublock_path:
                    print("âŒ Extension Not Found: uBlock Origin not found in 'extensions' folder.")
                    return False
                
                print(f"âœ… Found AdBlocker: {ublock_path}")
                
                # Find Chrome executable using config paths
                chrome_exe = None
                for path in self.chrome_paths:
                    if os.path.exists(path): 
                        chrome_exe = path
                        break
                        
                if not chrome_exe: 
                    print("âŒ Error: Google Chrome not found.")
                    return False
                    
                chrome_cmd = [
                    chrome_exe, 
                    f"--remote-debugging-port={self.debugging_port}", 
                    f"--user-data-dir={os.path.join(os.getcwd(), self.user_data_dir)}",
                    "--no-first-run", 
                    "--window-size=1280,720", 
                    "--remote-allow-origins=*",
                    f"--load-extension={ublock_path}", 
                    url
                ]
                
                print("ðŸ”§ JSON Generator: Launching fresh Chrome with AdBlocker and DevTools...")
                self.chrome_process = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                print(f"â³ JSON Generator: Waiting {self.page_load_wait}s for fresh Chrome and AdBlocker to initialize...")
                time.sleep(self.page_load_wait)
                
                game_window = None
                for _ in range(10): 
                    possible_windows = gw.getWindowsWithTitle('cocrea.world')
                    if not possible_windows: 
                        possible_windows = gw.getWindowsWithTitle('Chrome')
                    if possible_windows: 
                        game_window = possible_windows[0]
                        break
                    time.sleep(1)
                    
                if not game_window: 
                    print("âŒ Setup Error: Could not find the game window.")
                    return False
                    
                print(f"ðŸŽ¯ JSON Generator: Found fresh game window: '{game_window.title}'. Activating...")
                game_window.activate()
                time.sleep(0.5)
                game_window.maximize()
                time.sleep(2)
                
                # ================================================================
                # ðŸ” SMART VERIFICATION SYSTEM (EXACT COPY FROM sprunki_game_player.py)
                # ================================================================
                print(f"\nðŸ” JSON GENERATOR - STARTING SMART VERIFICATION SYSTEM")
                print("="*70)
                
                # Phase 1: Page Load Verification - CHá»œ CHO Äáº¾N KHI ready hoáº·c timeout
                print("ðŸ” Phase 1: Smart Page Load Verification for JSON Generator...")
                page_verification_success = self.verify_page_fully_loaded(url)
                
                if not page_verification_success:
                    print("âš ï¸ JSON Generator page verification failed, but continuing with workflow...")
                
                # Phase 2: Game Components Verification - CHá»œ CHO Äáº¾N KHI ready hoáº·c timeout
                print("ðŸŽ® Phase 2: Smart Game Components Verification for JSON Generator...")
                game_verification_success = self.verify_game_components_ready()
                
                if not game_verification_success:
                    print("âš ï¸ JSON Generator game verification failed, but continuing with workflow...")
                
                # Phase 3: Stabilization wait (EXACT LOGIC FROM sprunki_game_player.py)
                if page_verification_success and game_verification_success:
                    print(f"âœ… JSON GENERATOR VERIFICATION SUCCESSFUL: Page and game fully loaded!")
                    print(f"â³ JSON Generator stabilization wait: {self.stabilization_wait}s for optimal Run button detection...")
                    time.sleep(self.stabilization_wait)
                else:
                    print(f"âš ï¸ JSON Generator verification had issues, using fallback stabilization...")
                    time.sleep(5)  # Fallback wait if verification failed
                
                print("ðŸŽ¯ JSON Generator smart verification completed - starting enhanced Run button detection...")
                print("="*70)
                
                # ================================================================
                # ðŸš€ ENHANCED RUN BUTTON DETECTION (ORIGINAL LOGIC PRESERVED)
                # ================================================================
                
                detector = ImageDetector("cocrea.world")
                run_button_clicked = False
                max_retries = 3
                
                for attempt in range(1, max_retries + 1):
                    print(f"\n--- 🏗️ JSON Generator Finding 'Run' Button - Attempt {attempt}/{max_retries} ---")
                    
                    if attempt > 1:
                        print("   -> JSON Generator Action: Reloading page to clear potential issues...")
                        pyautogui.hotkey('ctrl', 'r')
                        
                        # Quick re-verification after reload
                        print("   -> JSON Generator Quick re-verification after reload...")
                        time.sleep(3)  # Brief wait for reload
                        quick_verification = self._execute_verification_js("document.readyState")
                        if quick_verification == 'complete':
                            print("   -> ✅ JSON Generator Page reloaded successfully")
                            time.sleep(self.stabilization_wait)  # Smart stabilization
                        else:
                            print("   -> ⚠️ JSON Generator Page reload uncertain, using fallback wait...")
                            time.sleep(8)  # Fallback wait
                    else:
                        # First attempt - verification already done above
                        print("   -> JSON Generator Using smart verification results (no additional wait needed)")
                    
                    # 🔧 SERVICE MODE FIX: Ensure window activation before clicking Run button
                    print("   -> 🔧 JSON Generator: Ensuring window activation for service mode...")
                    try:
                        possible_windows = gw.getWindowsWithTitle('cocrea.world')
                        if not possible_windows: 
                            possible_windows = gw.getWindowsWithTitle('Chrome')
                        if possible_windows:
                            target_window = possible_windows[0]
                            target_window.activate()
                            time.sleep(0.5)
                            print(f"   -> ✅ Window re-activated: '{target_window.title}'")
                        else:
                            print("   -> ⚠️ Could not find window to activate")
                    except Exception as e:
                        print(f"   -> ⚠️ Window activation failed: {e}")
                    
                    print("   -> 🔍 JSON Generator Searching for 'Run' button on screen...")
                    
                    # KEEP ORIGINAL IMAGE DETECTION LOGIC EXACTLY AS-IS
                    if detector.click_run_button(max_pagedown_attempts=self.max_pagedown_attempts):
                        print(f"âœ… JSON Generator SUCCESS: 'Run' button found and clicked on attempt {attempt}.")
                        run_button_clicked = True
                        break
                    else:
                        print(f"âš ï¸ JSON Generator FAILED: 'Run' button not found on attempt {attempt}.")
                        if attempt < max_retries:
                            print("   -> Preparing for next attempt...")
                        else:
                            print(f"âŒ Setup Error: Could not find 'Run' button after {max_retries} attempts.")
                            return False
                            
                if not run_button_clicked: 
                    return False
                    
                print(f"\nâ³ JSON Generator: Waiting {self.game_load_wait}s for game to load after clicking Run...")
                time.sleep(self.game_load_wait)
                
                # ================================================================
                # ðŸ“º FULLSCREEN BUTTON DETECTION (ORIGINAL LOGIC PRESERVED)
                # ================================================================
                
                # Click fullscreen button - KEEP ORIGINAL LOGIC EXACTLY AS-IS
                if detector.click_fullscreen_button(wait_time=self.fullscreen_wait):
                    print("âœ… JSON Generator: Clicked game's own fullscreen button.")
                    time.sleep(2)
                else:
                    print("âš ï¸ JSON Generator: Fullscreen button not found, continuing anyway.")
                    
                # Press F11 for true fullscreen
                print("âŒ¨ï¸ JSON Generator: Pressing F11 for true fullscreen mode...")
                pyautogui.press('f11')
                time.sleep(3)
                
                print("âœ… JSON Generator: Game setup completed with Smart Verification System. Ready for HYBRID JSON generation.")
                return True
                
            except Exception as e:
                print(f"âŒ Setup Error: Failed to prepare game: {e}")
                print(f"âŒš JSON Generator failed to navigate and prepare game: {e}")
                self.cleanup_chrome_complete()
                return False

    def _get_websocket_url(self):
        if self.ws_url: return self.ws_url
        try:
            response = requests.get("http://localhost:9222/json", timeout=5)
            tabs = response.json()
            target_tab = next((t for t in tabs if t.get('type') == 'page' and 'cocrea.world' in t.get('url', '')), None)
            if not target_tab: 
                print("âŒ CDP Error: Could not find cocrea.world tab.")
                return None
            self.ws_url = target_tab['webSocketDebuggerUrl']
            return self.ws_url
        except Exception as e:
            print(f"Ã¢ÂÅ’ Could not get WebSocket URL: {e}")
            return None

    def generate_complete_json(self, url, output_path=None):
        """
        Simplified Complete Logic: A vs B vs C
        
        1. Capture A (screenshot + phase) - INITIAL STATE
        2. Drag icon 19 Ã¢â€ â€™ Capture B (screenshot + phase) - AFTER ICON 19
           - Compare phase A vs B Ã¢â€ â€™ if hasHorror = True Ã¢â€ â€™ STOP
           - Compare screenshot A vs B Ã¢â€ â€™ if hasHorror = True Ã¢â€ â€™ STOP
        3. Find highest index icon Ã¢â€ â€™ Drag Ã¢â€ â€™ Capture C (screenshot + phase) - AFTER HIGHEST INDEX
           - Compare phase A vs C Ã¢â€ â€™ if hasHorror = True Ã¢â€ â€™ STOP  
           - Compare screenshot A vs C Ã¢â€ â€™ if hasHorror = True Ã¢â€ â€™ STOP
        4. Final result = False
        
        STOP immediately when hasHorror = True is detected
        """
        try:
            print("\nÃ°Å¸Å¡â‚¬ Starting SIMPLIFIED COMPLETE LOGIC (A vs B vs C)")
            print("="*70)
            print("Logic: A(initial) vs B(icon19) vs C(highest) - Stop when hasHorror=True")
            print("="*70)
            
            # Setup game
            if not self.navigate_and_prepare_game(url): 
                return False, None, None
                
            ws_url = self._get_websocket_url()
            if not ws_url: 
                return False, None, None
                
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print("Ã°Å¸Â§Âª Testing JavaScript environment...")
            test_response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": "window.location.href", "returnByValue": True}
            })
            if test_response is None or 'result' not in test_response:
                print("Ã¢ÂÅ’ JavaScript environment test failed")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
            print("Ã¢Å“â€¦ JavaScript environment ready")
            
            print("Ã°Å¸â€“Â±Ã¯Â¸Â Skipping intros via CDP...")
            # ============================
            # MULTI-TARGET SKIP INTRO: Click ALL Skip Intro buttons + Blind Clicks
            # ============================
            print("\nÃ°Å¸â€Â MULTI-TARGET SKIP INTRO: Finding and clicking ALL Skip Intro buttons...")

            # Phase 1: Find ALL Skip Intro buttons
            print("Ã°Å¸â€œÂ Phase 1: Searching for ALL Skip Intro buttons...")

            skip_intro_response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": self.js_find_skip_intro, "awaitPromise": True, "returnByValue": True}
            })

            smart_clicks_performed = 0
            all_buttons = []

            if skip_intro_response and 'result' in skip_intro_response:
                skip_result = skip_intro_response.get('result', {}).get('result', {}).get('value')
                
                if skip_result and skip_result.get('found'):
                    all_buttons = skip_result.get('buttons', [])
                    button_count = skip_result.get('count', 0)
                    visible_count = skip_result.get('visibleCount', 0)
                    hidden_count = skip_result.get('hiddenCount', 0)
                    
                    print(f"Ã¢Å“â€¦ Found {button_count} Skip Intro buttons:")
                    print(f"   Ã°Å¸â€œÅ  Visible: {visible_count}, Hidden: {hidden_count}")
                    
                    # Click ALL buttons found
                    for i, button in enumerate(all_buttons):
                        button_num = i + 1
                        x, y = button['x'], button['y']
                        visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                        costume_name = button.get('currentCostumeName', 'Unknown')
                        
                        print(f"Ã°Å¸Å½Â¯ Clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status} - {costume_name}")
                        
                        # Click this specific button
                        self._simulate_click_via_cdp(ws, x, y)
                        time.sleep(0.8)  # Wait between clicks
                        
                        smart_clicks_performed += 1
                    
                    print(f"Ã¢Å“â€¦ Smart clicks completed: {smart_clicks_performed} buttons clicked!")
                    
                else:
                    reason = skip_result.get('reason', 'unknown') if skip_result else 'no_result'
                    print(f"Ã¢â€žÂ¹Ã¯Â¸Â No Skip Intro buttons found - Reason: {reason}")
                    print("Ã°Å¸â€â€ž Will use blind click method only...")

            # Phase 2: ALWAYS perform blind clicks for additional assurance
            if smart_clicks_performed > 0:
                print("Ã°Å¸â€â€ž Phase 2: Performing ADDITIONAL blind clicks for triple protection...")
                print("   Ã¢â€ â€™ This ensures intro skipping even if smart clicks had edge cases")
            else:
                print("Ã°Å¸â€“Â±Ã¯Â¸Â Phase 2: Performing STANDARD blind clicks (no smart buttons found)...")

            # Perform blind clicks regardless of smart click success
            for i in range(self.intro_skip_clicks):  # Use config value
                self._simulate_click_via_cdp(ws, 960, 540)
                time.sleep(0.5)

            # Summary
            if smart_clicks_performed > 0:
                print("Ã°Å¸Å½â€° MULTI-TARGET SKIP INTRO COMPLETED:")
                print(f"   Ã¢Å“â€¦ Smart clicks: {smart_clicks_performed} buttons clicked successfully")
                print(f"   Ã¢Å“â€¦ Button details: {len(all_buttons)} total found")
                for i, button in enumerate(all_buttons):
                    visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                    print(f"      {i+1}. ({button['x']}, {button['y']}) - {visible_status}")
                print(f"   Ã¢Å“â€¦ Blind clicks: {self.intro_skip_clicks} additional clicks performed")
                print("   Ã°Å¸â€ºÂ¡Ã¯Â¸Â Triple protection ensures maximum skip success rate")
            else:
                print("Ã°Å¸Å½â€° STANDARD SKIP INTRO COMPLETED:")
                print("   Ã¢Å¡ Ã¯Â¸Â Smart clicks: N/A (No Skip Intro buttons found)")
                print(f"   Ã¢Å“â€¦ Blind clicks: {self.intro_skip_clicks} clicks performed")
                print("   Ã°Å¸â€â€ž Standard method used as fallback")

            print("Ã°Å¸Å½Â® Skip intro phase finished - Game should be ready for interaction")
            
            # Initialize final result variables
            final_has_horror = False
            final_confidence = 0
            final_detection_method = "no_horror_detected"
            final_visual_result = {}
            
            # ============================
            # CAPTURE A: Initial State (Screenshot + Phase)
            # ============================
            print("\n" + "="*70)
            print("Ã°Å¸â€œÂ¸ CAPTURE A: Initial State (Screenshot + Phase)")
            print("="*70)
            
            print("Ã°Å¸â€œÅ  Extracting initial state A + coordinates...")
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": self.js_get_state_and_coordinates, "awaitPromise": True, "returnByValue": True}
            })
            
            if response is None or 'result' not in response:
                print("Ã¢ÂÅ’ Failed to get initial state A")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            state_A = response.get('result', {}).get('result', {}).get('value')
            if not state_A or "error" in state_A: 
                print(f"Ã¢ÂÅ’ Failed to get initial state A: {state_A.get('error', 'Unknown') if state_A else 'None'}")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
            
            phase_A = self.find_phase_variable(state_A)
            print(f"Ã¢Å“â€¦ State A captured - Phase A: {phase_A}")
            
            # ============================
            # CRITICAL CHECK: Verify icon count before proceeding
            # ============================
            icons_count = len(state_A.get('icons', []))
            print(f"\nÃ°Å¸â€œÅ  ICON COUNT VERIFICATION: Found {icons_count} icons")
            
            if icons_count < 20:
                print(f"Ã¢Å¡ Ã¯Â¸Â INSUFFICIENT ICONS: Only {icons_count} icons found (need 20+ for icon index 19)")
                print("Ã°Å¸â€Å¡ EARLY EXIT: Setting hasHorror = False due to insufficient icons")
                
                # Get final layout for JSON generation
                print("Ã°Å¸â€œÅ  Extracting final full layout...")
                response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": self.js_full_layout, "awaitPromise": True, "returnByValue": True}
                })
                
                if response and 'result' in response:
                    layout_state = response.get('result', {}).get('result', {}).get('value')
                    if layout_state and "error" not in layout_state:
                        print("Ã¢Å“â€¦ Final full layout captured successfully")
                        
                        ws.close()
                        
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
                        
                        print("Ã°Å¸â€™Â¾ Saving JSON file...")
                        json_file_path = self.save_json_file(final_json, output_path, custom_title=page_title)
                        
                        if json_file_path:
                            print(f"\nÃ°Å¸Å½â€° EARLY EXIT COMPLETED - INSUFFICIENT ICONS!")
                            print("="*70)
                            print(f"Ã¢Å“â€¦ hasHorror Detection: False (insufficient icons)")
                            print(f"Ã¢Å“â€¦ Icons Found: {icons_count}/20")
                            print(f"Ã¢Å“â€¦ Reason: Need icon index 19 for horror detection")
                            print("="*70)
                            
                            self.cleanup_chrome_complete()
                            return True, json_file_path, final_json
                        else:
                            print("Ã¢ÂÅ’ Failed to save JSON file")
                            ws.close()
                            self.cleanup_chrome_complete()
                            return False, None, None
                    else:
                        print("Ã¢ÂÅ’ Failed to get final layout for insufficient icons case")
                        ws.close()
                        self.cleanup_chrome_complete()
                        return False, None, None
                else:
                    print("Ã¢ÂÅ’ Failed to execute JS for final layout")
                    ws.close()
                    self.cleanup_chrome_complete()
                    return False, None, None
            else:
                print(f"Ã¢Å“â€¦ SUFFICIENT ICONS: {icons_count} icons found - proceeding with horror detection")
            
            # Capture screenshot A
            extended_area = self.calculate_extended_screenshot_area(state_A)
            screenshot_A = None
            if extended_area:
                screenshot_A = self.capture_icons_area_screenshot(extended_area)
                if screenshot_A is not None:
                    print(f"Ã¢Å“â€¦ Screenshot A captured - Size: {screenshot_A.shape}")
                else:
                    print("Ã¢Å¡ Ã¯Â¸Â Failed to capture screenshot A")
            else:
                print("Ã¢Å¡ Ã¯Â¸Â Could not calculate extended area for screenshot A")
            
            # ============================
            # DRAG ICON 19 & CAPTURE B: After Icon 19 (Screenshot + Phase)
            # ============================
            print("\n" + "="*70)
            print("Ã°Å¸â€“Â±Ã¯Â¸Â DRAG ICON 19 & CAPTURE B: After Icon 19 (Screenshot + Phase)")
            print("="*70)
            
            drag_coords = state_A.get('dragCoordinates', {})
            icon_coords = drag_coords.get('icon')
            slot_coords = drag_coords.get('slot')
            
            # --- ENHANCED DEBUG LOGGING FOR DRAG COORDINATES ---
            # Log the retrieved coordinates for inspection
            print("\nÃ°Å¸â€Â¬ DEBUG: Checking Drag Coordinates from State A...")
            print(f"   - Raw icon_coords (icon 19): {icon_coords}")
            print(f"   - Raw slot_coords (slot 3): {slot_coords}")

            # Detailed validation
            is_icon_valid = icon_coords and icon_coords.get('x') is not None and icon_coords.get('y') is not None
            is_slot_valid = slot_coords and slot_coords.get('x') is not None and slot_coords.get('y') is not None
            
            if not is_icon_valid or not is_slot_valid:
                print("Ã¢ÂÅ’ FATAL: Missing or invalid drag coordinates. Cannot proceed with horror detection.")
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
                
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
            # --- END OF ENHANCED DEBUG LOGGING ---

            print(f"Ã°Å¸Å½Â¯ Dragging icon 19 from ({icon_coords['x']}, {icon_coords['y']}) to ({slot_coords['x']}, {slot_coords['y']})")
            self._simulate_drag_via_cdp(ws, icon_coords['x'], icon_coords['y'], slot_coords['x'], slot_coords['y'])
            
            wait_time = 25
            print(f"Ã¢ÂÂ³ Waiting {wait_time} seconds for transition...")
            time.sleep(wait_time)
            
            print("Ã°Å¸â€œÅ  Extracting state B after icon 19...")
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": self.js_get_state_and_coordinates, "awaitPromise": True, "returnByValue": True}
            })
            
            if response is None or 'result' not in response:
                print("Ã¢ÂÅ’ Failed to get state B")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            state_B = response.get('result', {}).get('result', {}).get('value')
            if not state_B or "error" in state_B: 
                print(f"Ã¢ÂÅ’ Failed to get state B: {state_B.get('error', 'Unknown') if state_B else 'None'}")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
            
            phase_B = self.find_phase_variable(state_B)
            print(f"Ã¢Å“â€¦ State B captured - Phase B: {phase_B}")
            
            # Capture screenshot B
            screenshot_B = None
            if extended_area:
                screenshot_B = self.capture_icons_area_screenshot(extended_area)
                if screenshot_B is not None:
                    print(f"Ã¢Å“â€¦ Screenshot B captured - Size: {screenshot_B.shape}")
                else:
                    print("Ã¢Å¡ Ã¯Â¸Â Failed to capture screenshot B")
            
            # ============================
            # COMPARE A vs B: Phase Comparison
            # ============================
            print("\n" + "="*70)
            print("Ã°Å¸â€Â COMPARE A vs B: Phase Comparison")
            print("="*70)
            
            print(f"Ã°Å¸â€œÅ  Comparing Phase A ({phase_A}) vs Phase B ({phase_B})")
            
            # Phase comparison logic
            phase_A_str = str(phase_A) if phase_A is not None else None
            phase_B_str = str(phase_B) if phase_B is not None else None
            
            # Initialize flags for this comparison round
            horror_detected_phase_AB = False
            
            if phase_A_str is not None and phase_B_str is not None:
                has_horror_phase_AB = (phase_A_str == "1" and phase_B_str == "2")
                
                if has_horror_phase_AB:
                    print(f"Ã°Å¸Å½â€° SUCCESS! Phase A vs B: {phase_A_str} Ã¢â€ â€™ {phase_B_str} = hasHorror TRUE")
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
                    print("Ã°Å¸â€ºâ€˜ STOP: Horror detected via phase A vs B")
                else:
                    print(f"Ã¢â€žÂ¹Ã¯Â¸Â No horror in phase A vs B: {phase_A_str} Ã¢â€ â€™ {phase_B_str}")
            else:
                print("Ã¢Å¡ Ã¯Â¸Â Cannot compare phases A vs B (missing phase data)")
                print("Ã¢â€žÂ¹Ã¯Â¸Â Will proceed to screenshot comparison")
            
            # ============================
            # COMPARE A vs B: Screenshot Comparison (ALWAYS TRY)
            # ============================
            if not horror_detected_phase_AB:  # Only if phase didn't detect horror
                print("\n" + "="*50)
                print("Ã°Å¸â€œÂ¸ COMPARE A vs B: Screenshot Comparison")
                print("="*50)
                
                if screenshot_A is not None and screenshot_B is not None:
                    print("Ã°Å¸â€Â Comparing Screenshot A vs Screenshot B...")
                    visual_result_AB = self.detect_horror_extended_area(screenshot_A, screenshot_B)
                    
                    if visual_result_AB and visual_result_AB['has_horror']:
                        print(f"Ã°Å¸Å½â€° SUCCESS! Screenshot A vs B detected hasHorror TRUE")
                        final_has_horror = True
                        final_confidence = visual_result_AB['confidence']
                        final_detection_method = f"screenshot_A_vs_B_{visual_result_AB['method']}_icon_19"
                        final_visual_result = visual_result_AB
                        final_visual_result['horror_icon_index'] = 19
                        final_visual_result['comparison'] = 'A_vs_B'
                        # STOP - Horror detected!
                        print("Ã°Å¸â€ºâ€˜ STOP: Horror detected via screenshot A vs B")
                    else:
                        print("Ã¢â€žÂ¹Ã¯Â¸Â No horror in screenshot A vs B")
                else:
                    print("Ã¢Å¡ Ã¯Â¸Â Cannot compare screenshots A vs B (missing screenshot data)")
            
            # ============================
            # IF NO HORROR YET: Try Highest Index Icon
            # ============================
            if not final_has_horror:
                print("\n" + "="*70)
                print("Ã°Å¸â€Â NO HORROR YET: Try Highest Index Icon")
                print("="*70)
                
                # Get current layout to find highest index icon
                current_layout_response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": self.js_full_layout, "awaitPromise": True, "returnByValue": True}
                })

                if current_layout_response and 'result' in current_layout_response:
                    current_layout_data = current_layout_response.get('result', {}).get('result', {}).get('value')
                    
                    if current_layout_data:
                        available_icons = current_layout_data.get('layout', {}).get('icons', [])
                        valid_icons = [icon for icon in available_icons if icon.get('x') is not None and icon.get('y') is not None]
                        high_index_icons = [icon for icon in valid_icons if icon.get('index', 0) > 19]

                        if high_index_icons:
                            highest_index_icon = max(high_index_icons, key=lambda x: x.get('index', 0))
                            highest_index = highest_index_icon.get('index', 0)
                            
                            print(f"Ã°Å¸Å½Â¯ Found highest index icon: {highest_index}")
                            
                            available_slots = current_layout_data.get('layout', {}).get('slots', [])
                            slot_4_coords = next((slot for slot in available_slots if slot.get('index') == 4), None)
                            
                            if slot_4_coords and slot_4_coords.get('x') is not None:
                                # ============================
                                # DRAG HIGHEST INDEX & CAPTURE C: After Highest Index (Screenshot + Phase)
                                # ============================
                                print("\n" + "="*70)
                                print(f"Ã°Å¸â€“Â±Ã¯Â¸Â DRAG ICON {highest_index} & CAPTURE C: After Highest Index (Screenshot + Phase)")
                                print("="*70)
                                
                                print(f"Ã°Å¸Å½Â¯ Dragging icon {highest_index} from ({highest_index_icon['x']}, {highest_index_icon['y']}) to ({slot_4_coords['x']}, {slot_4_coords['y']})")
                                self._simulate_drag_via_cdp(
                                    ws, 
                                    highest_index_icon['x'], 
                                    highest_index_icon['y'], 
                                    slot_4_coords['x'], 
                                    slot_4_coords['y']
                                )
                                
                                wait_time = 25
                                print(f"Ã¢ÂÂ³ Waiting {wait_time}s for transition...")
                                time.sleep(wait_time)
                                
                                print("Ã°Å¸â€œÅ  Extracting state C after highest index...")
                                response = self._send_cdp_command(ws, {
                                    "method": "Runtime.evaluate", 
                                    "params": {"expression": self.js_get_state_and_coordinates, "awaitPromise": True, "returnByValue": True}
                                })
                                
                                if response and 'result' in response:
                                    state_C = response.get('result', {}).get('result', {}).get('value')
                                    
                                    if state_C:
                                        phase_C = self.find_phase_variable(state_C)
                                        print(f"Ã¢Å“â€¦ State C captured - Phase C: {phase_C}")
                                        
                                        # Capture screenshot C
                                        screenshot_C = None
                                        if extended_area:
                                            screenshot_C = self.capture_icons_area_screenshot(extended_area)
                                            if screenshot_C is not None:
                                                print(f"Ã¢Å“â€¦ Screenshot C captured - Size: {screenshot_C.shape}")
                                            else:
                                                print("Ã¢Å¡ Ã¯Â¸Â Failed to capture screenshot C")
                                        
                                        # Initialize flags for A vs C comparison
                                        horror_detected_phase_AC = False
                                        
                                        # ============================
                                        # COMPARE A vs C: Phase Comparison
                                        # ============================
                                        print("\n" + "="*70)
                                        print("Ã°Å¸â€Â COMPARE A vs C: Phase Comparison")
                                        print("="*70)
                                        
                                        print(f"Ã°Å¸â€œÅ  Comparing Phase A ({phase_A}) vs Phase C ({phase_C})")
                                        
                                        phase_C_str = str(phase_C) if phase_C is not None else None
                                        
                                        if phase_A_str is not None and phase_C_str is not None:
                                            has_horror_phase_AC = (phase_A_str == "1" and phase_C_str == "2")
                                            
                                            if has_horror_phase_AC:
                                                print(f"Ã°Å¸Å½â€° SUCCESS! Phase A vs C: {phase_A_str} Ã¢â€ â€™ {phase_C_str} = hasHorror TRUE")
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
                                                print("Ã°Å¸â€ºâ€˜ STOP: Horror detected via phase A vs C")
                                            else:
                                                print(f"Ã¢â€žÂ¹Ã¯Â¸Â No horror in phase A vs C: {phase_A_str} Ã¢â€ â€™ {phase_C_str}")
                                        else:
                                            print("Ã¢Å¡ Ã¯Â¸Â Cannot compare phases A vs C (missing phase data)")
                                            print("Ã¢â€žÂ¹Ã¯Â¸Â Will proceed to screenshot comparison")
                                        
                                        # ============================
                                        # COMPARE A vs C: Screenshot Comparison (ALWAYS TRY)
                                        # ============================
                                        if not horror_detected_phase_AC:  # Only if phase didn't detect horror
                                            print("\n" + "="*50)
                                            print("Ã°Å¸â€œÂ¸ COMPARE A vs C: Screenshot Comparison")
                                            print("="*50)
                                            
                                            if screenshot_A is not None and screenshot_C is not None:
                                                print("Ã°Å¸â€Â Comparing Screenshot A vs Screenshot C...")
                                                visual_result_AC = self.detect_horror_extended_area(screenshot_A, screenshot_C)
                                                
                                                if visual_result_AC and visual_result_AC['has_horror']:
                                                    print(f"Ã°Å¸Å½â€° SUCCESS! Screenshot A vs C detected hasHorror TRUE")
                                                    final_has_horror = True
                                                    final_confidence = visual_result_AC['confidence']
                                                    final_detection_method = f"screenshot_A_vs_C_{visual_result_AC['method']}_icon_{highest_index}"
                                                    final_visual_result = visual_result_AC
                                                    final_visual_result['horror_icon_index'] = highest_index
                                                    final_visual_result['comparison'] = 'A_vs_C'
                                                    # STOP - Horror detected!
                                                    print("Ã°Å¸â€ºâ€˜ STOP: Horror detected via screenshot A vs C")
                                                else:
                                                    print("Ã¢â€žÂ¹Ã¯Â¸Â No horror in screenshot A vs C")
                                                    print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (all methods exhausted)")
                                                    final_detection_method = "all_methods_exhausted"
                                            else:
                                                print("Ã¢Å¡ Ã¯Â¸Â Cannot compare screenshots A vs C (missing screenshot data)")
                                                print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (screenshot A vs C unavailable)")
                                                final_detection_method = "screenshot_A_vs_C_unavailable"
                                    else:
                                        print("Ã¢ÂÅ’ Failed to get state C")
                                        print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (failed to capture state C)")
                                        final_detection_method = "failed_state_C"
                                else:
                                    print("Ã¢ÂÅ’ Failed to execute JS for state C")
                                    print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (JS execution failed)")
                                    final_detection_method = "js_execution_failed"
                            else:
                                print("Ã¢ÂÅ’ Slot 4 coordinates not found")
                                print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (slot 4 unavailable)")
                                final_detection_method = "slot_4_unavailable"
                        else:
                            print("Ã¢â€žÂ¹Ã¯Â¸Â No icons with index > 19 available")
                            print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (no high index icons)")
                            final_detection_method = "no_high_index_icons"
                    else:
                        print("Ã¢ÂÅ’ Failed to get current layout data")
                        print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (layout data unavailable)")
                        final_detection_method = "layout_data_unavailable"
                else:
                    print("Ã¢ÂÅ’ Failed to fetch current layout")
                    print("Ã°Å¸â€Å¡ FINAL RESULT: hasHorror = False (layout fetch failed)")
                    final_detection_method = "layout_fetch_failed"
            
            # ============================
            # FINAL RESULT SUMMARY
            # ============================
            print("\n" + "="*70)
            print("Ã°Å¸Å½Â¯ FINAL DETECTION RESULT SUMMARY")
            print("="*70)
            print(f"hasHorror: {final_has_horror}")
            print(f"Method: {final_detection_method}")
            print(f"Confidence: {final_confidence:.3f}")
            if 'horror_icon_index' in final_visual_result:
                print(f"Horror Icon Index: {final_visual_result['horror_icon_index']}")
            if 'comparison' in final_visual_result:
                print(f"Comparison: {final_visual_result['comparison']}")
            if 'phase_before' in final_visual_result and 'phase_after' in final_visual_result:
                print(f"Phase Transition: {final_visual_result['phase_before']} Ã¢â€ â€™ {final_visual_result['phase_after']}")
            print("="*70)
            
            # ============================
            # Full Layout Capture
            # ============================
            print("\n" + "="*70)
            print("Ã°Å¸â€œÅ  Full Layout Capture")
            print("="*70)
            
            print("Ã°Å¸â€œÅ  Extracting final full layout...")
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": self.js_full_layout, "awaitPromise": True, "returnByValue": True}
            })
            
            if response is None or 'result' not in response:
                print("Ã¢ÂÅ’ Failed to get final full layout")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            layout_state = response.get('result', {}).get('result', {}).get('value')
            if not layout_state or "error" in layout_state: 
                print(f"Ã¢ÂÅ’ Failed to get final layout: {layout_state.get('error', 'Unknown') if layout_state else 'None'}")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            print("Ã¢Å“â€¦ Final full layout captured successfully")
            
            ws.close()
            
            # ============================
            # Generate Final JSON
            # ============================
            print("\n" + "="*70)
            print("Ã°Å¸â€œÂ Generate Final JSON")
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
                        "logic_flow": "A(initial) Ã¢â€ â€™ B(icon19) Ã¢â€ â€™ C(highest) with early stop"
                    }
                },
                "layout": layout_state.get('layout', {}),
                "settings": {
                    "aspectRatio": "96:83"
                }
            }
            
            print("Ã°Å¸â€™Â¾ Saving JSON file...")
            json_file_path = self.save_json_file(final_json, output_path, custom_title=page_title)
            if not json_file_path: 
                self.cleanup_chrome_complete()
                return False, None, None
                
            print("\nÃ°Å¸Å½â€° SIMPLIFIED COMPLETE LOGIC COMPLETED!")
            print("="*70)
            print(f"Ã¢Å“â€¦ hasHorror Detection: {final_has_horror}")
            print(f"Ã¢Å“â€¦ Detection Method: {final_detection_method}")
            print(f"Ã¢Å“â€¦ Logic Flow: A(initial) vs B(icon19) vs C(highest) with early stop")
            if 'comparison' in final_visual_result:
                print(f"Ã¢Å“â€¦ Successful Comparison: {final_visual_result['comparison']}")
            print("="*70)
            
            print("Ã°Å¸Â§Â¹ Performing complete cleanup...")
            self.cleanup_chrome_complete()
                
            return True, json_file_path, final_json
            
        except Exception as e:
            print(f"Ã¢ÂÅ’ Simplified complete logic error: {e}")
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
            # LÃ¡ÂºÂ¥y json_output_directory tÃ¡Â»Â« config
            json_output_dir = self.config.get('output', {}).get('json_output_directory', r"I:\VS-Project\Sprunki\JSON")
            os.makedirs(json_output_dir, exist_ok=True)

            if not output_path:
                import re
                
                if custom_title and custom_title.strip():
                    base_name = custom_title.strip()
                    print(f"Ã°Å¸ÂÂ·Ã¯Â¸Â Using custom title for filename: '{base_name}'")
                else:
                    from urllib.parse import urlparse
                    slug_base = urlparse(self.game_url).path.split('/')[-1]
                    if not slug_base: 
                        slug_base = urlparse(self.game_url).netloc.replace('www.','')
                    base_name = slug_base
                    print(f"Ã°Å¸ÂÂ·Ã¯Â¸Â Using URL slug for filename: '{base_name}'")
                
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
            print(f"Ã°Å¸â€™Â¾ JSON saved: {output_path}")
            return output_path
        except Exception as e:
            print(f"Ã¢ÂÅ’ Save JSON error: {e}")
            import traceback
            traceback.print_exc()
            return None
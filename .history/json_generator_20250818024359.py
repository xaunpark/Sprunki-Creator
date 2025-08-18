# json_generator.py (v11.0 - HYBRID - Phase Comparison + Screenshot Fallback)
# UPGRADED: Phase value comparison as primary method with visual fallback

import json
import time
import os
import subprocess
import requests
from datetime import datetime
import pyautogui
from tkinter import messagebox
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
    print("âš ï¸ OpenCV not available - using basic image comparison")

try:
    from skimage.metrics import structural_similarity as ssim
    HAS_SSIM = True
except ImportError:
    HAS_SSIM = False
    print("âš ï¸ scikit-image not available - using custom SSIM implementation")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("âš ï¸ PIL not available - using pyautogui screenshots only")

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
                // UPDATED: Hybrid Logic cho Icons (Logic Má»›i)
                // =====================================================
                console.log("ðŸš€ Báº¯t Ä‘áº§u quÃ¡ trÃ¬nh láº¥y icons (Logic Hybrid - ChÃ­nh xÃ¡c nháº¥t)...");

                // BÆ¯á»šC 1: Láº¤Y CÃC ICON ÄANG HIá»‚N THá»Š
                console.log("â–¶ï¸ BÆ°á»›c 1: TÃ¬m cÃ¡c sprite 'Icons' Ä‘ang hiá»ƒn thá»‹...");
                const visibleIcons = allTargets.filter(t => t.sprite.name === 'Icons' && t.visible);
                console.log(`âœ… TÃ¬m tháº¥y ${visibleIcons.length} icons trÃªn mÃ n hÃ¬nh.`);

                // BÆ¯á»šC 2: Táº O "DANH Báº " SPRITE (chá»‰ loáº¡i bá» Stage)
                console.log("â–¶ï¸ BÆ°á»›c 2: Táº¡o 'danh báº¡' gá»“m táº¥t cáº£ cÃ¡c sprite (trá»« Stage) Ä‘á»ƒ tra cá»©u...");
                const allSprites = allTargets.filter(t => !t.isStage);
                console.log(`âœ… 'Danh báº¡' cÃ³ ${allSprites.length} sprite.`);

                // BÆ¯á»šC 3: Sáº®P Xáº¾P ICONS Dá»°A TRÃŠN INDEX Cá»¦A SPRITE TÆ¯Æ NG á»¨NG
                console.log("â–¶ï¸ BÆ°á»›c 3: Sáº¯p xáº¿p icons báº±ng cÃ¡ch liÃªn káº¿t ngÆ°á»£c láº¡i vá»›i sprite gá»‘c...");
                visibleIcons.sort((iconA, iconB) => {
                    // Láº¥y 'chÃ¬a khÃ³a' tá»« má»—i icon
                    const charIndexA = Math.floor(iconA.currentCostume / 3);
                    const charIndexB = Math.floor(iconB.currentCostume / 3);

                    // DÃ¹ng 'chÃ¬a khÃ³a' Ä‘á»ƒ tra cá»©u trong 'danh báº¡'
                    const linkedSpriteA = allSprites[charIndexA];
                    const linkedSpriteB = allSprites[charIndexB];

                    // Náº¿u vÃ¬ lÃ½ do nÃ o Ä‘Ã³ khÃ´ng tÃ¬m tháº¥y sprite, khÃ´ng sáº¯p xáº¿p
                    if (!linkedSpriteA || !linkedSpriteB) {
                        console.warn(`KhÃ´ng thá»ƒ tÃ¬m tháº¥y sprite liÃªn káº¿t cho index ${charIndexA} hoáº·c ${charIndexB}`);
                        return 0;
                    }

                    // Láº¥y 'sá»‘ bÃ¡o danh' (runtime index) cá»§a sprite Ä‘Ã£ liÃªn káº¿t
                    const runtimeIndexA = allTargets.indexOf(linkedSpriteA);
                    const runtimeIndexB = allTargets.indexOf(linkedSpriteB);

                    // So sÃ¡nh 'sá»‘ bÃ¡o danh' Ä‘á»ƒ sáº¯p xáº¿p
                    return runtimeIndexA - runtimeIndexB;
                });

                // BÆ¯á»šC 4: Táº O Máº¢NG Káº¾T QUáº¢ CUá»I CÃ™NG
                console.log("â–¶ï¸ BÆ°á»›c 4: Chuyá»ƒn Ä‘á»•i tá»a Ä‘á»™ vÃ  gÃ¡n index tuáº§n tá»± má»›i...");
                const icons = visibleIcons.map((icon, index) => {
                    const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                    return {
                        x: pixelCoords.x,
                        y: pixelCoords.y,
                        index: index // GÃ¡n index tuáº§n tá»± 0, 1, 2... sau khi Ä‘Ã£ sáº¯p xáº¿p
                    };
                });

                console.log("âœ… HOÃ€N THÃ€NH! Icons Ä‘Æ°á»£c táº¡o theo logic Hybrid má»›i:");
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
                console.log("ðŸ” Searching for ALL Skip Intro buttons (multiple detection)...");
                
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
                    console.log("âŒ VM not found - cannot search for Skip Intro buttons");
                    resolve({ found: false, count: 0, buttons: [], reason: "vm_not_found" });
                    return;
                }
                
                const allTargets = vm.runtime.targets.map(t => t);
                console.log(`ðŸŽ¯ Total targets found: ${allTargets.length}`);
                
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
                
                console.log(`ðŸ” Found ${introTargets.length} 'Intro' targets with 'Skip' costume`);
                
                if (introTargets.length === 0) {
                    console.log("â„¹ï¸ No Skip Intro buttons found");
                    
                    // DEBUG: Check for alternative patterns
                    const skipCostumeTargets = allTargets.filter(target => 
                        target.sprite.costumes.some(costume => costume.name === "Skip")
                    );
                    
                    if (skipCostumeTargets.length > 0) {
                        console.log(`ðŸ” Found ${skipCostumeTargets.length} targets with 'Skip' costume but different names:`);
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
                    console.log(`\\nðŸŽ¯ Processing Intro Target ${index + 1}/${introTargets.length}:`);
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
                        
                        console.log(`   âœ… Converted to pixel coordinates: (${pixelCoords.x}, ${pixelCoords.y})`);
                        console.log(`   ðŸ“‹ Added to click list (${target.visible ? 'VISIBLE' : 'HIDDEN'})`);
                    } else {
                        console.log(`   âŒ Coordinate conversion failed for target ${index + 1}`);
                    }
                });
                
                // Summary
                const visibleButtons = skipIntroButtons.filter(btn => btn.visible);
                const hiddenButtons = skipIntroButtons.filter(btn => !btn.visible);
                
                console.log(`\\nðŸ“Š SKIP INTRO BUTTONS SUMMARY:`);
                console.log(`   - Total found: ${skipIntroButtons.length}`);
                console.log(`   - Visible: ${visibleButtons.length}`);
                console.log(`   - Hidden: ${hiddenButtons.length}`);
                
                if (skipIntroButtons.length > 0) {
                    console.log(`\\nðŸŽ¯ Will click ALL ${skipIntroButtons.length} buttons to ensure success:`);
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
                console.error("âŒ Error searching for Skip Intro buttons:", e);
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
                // UPDATED: Function getIconsWithHybridLogic (Logic Má»›i)
                // =====================================================
                function getIconsWithHybridLogic(allTargets) {
                    console.log("ðŸš€ Báº¯t Ä‘áº§u quÃ¡ trÃ¬nh láº¥y icons (Logic Hybrid - ChÃ­nh xÃ¡c nháº¥t)...");

                    // BÆ¯á»šC 1: Láº¤Y CÃC ICON ÄANG HIá»‚N THá»Š
                    console.log("â–¶ï¸ BÆ°á»›c 1: TÃ¬m cÃ¡c sprite 'Icons' Ä‘ang hiá»ƒn thá»‹...");
                    const visibleIcons = allTargets.filter(t => t.sprite.name === 'Icons' && t.visible);
                    console.log(`âœ… TÃ¬m tháº¥y ${visibleIcons.length} icons trÃªn mÃ n hÃ¬nh.`);

                    // BÆ¯á»šC 2: Táº O "DANH Báº " SPRITE (chá»‰ loáº¡i bá» Stage)
                    console.log("â–¶ï¸ BÆ°á»›c 2: Táº¡o 'danh báº¡' gá»“m táº¥t cáº£ cÃ¡c sprite (trá»« Stage) Ä‘á»ƒ tra cá»©u...");
                    const allSprites = allTargets.filter(t => !t.isStage);
                    console.log(`âœ… 'Danh báº¡' cÃ³ ${allSprites.length} sprite.`);

                    // BÆ¯á»šC 3: Sáº®P Xáº¾P ICONS Dá»°A TRÃŠN INDEX Cá»¦A SPRITE TÆ¯Æ NG á»¨NG
                    console.log("â–¶ï¸ BÆ°á»›c 3: Sáº¯p xáº¿p icons báº±ng cÃ¡ch liÃªn káº¿t ngÆ°á»£c láº¡i vá»›i sprite gá»‘c...");
                    visibleIcons.sort((iconA, iconB) => {
                        // Láº¥y 'chÃ¬a khÃ³a' tá»« má»—i icon
                        const charIndexA = Math.floor(iconA.currentCostume / 3);
                        const charIndexB = Math.floor(iconB.currentCostume / 3);

                        // DÃ¹ng 'chÃ¬a khÃ³a' Ä‘á»ƒ tra cá»©u trong 'danh báº¡'
                        const linkedSpriteA = allSprites[charIndexA];
                        const linkedSpriteB = allSprites[charIndexB];

                        // Náº¿u vÃ¬ lÃ½ do nÃ o Ä‘Ã³ khÃ´ng tÃ¬m tháº¥y sprite, khÃ´ng sáº¯p xáº¿p
                        if (!linkedSpriteA || !linkedSpriteB) {
                            console.warn(`KhÃ´ng thá»ƒ tÃ¬m tháº¥y sprite liÃªn káº¿t cho index ${charIndexA} hoáº·c ${charIndexB}`);
                            return 0;
                        }

                        // Láº¥y 'sá»‘ bÃ¡o danh' (runtime index) cá»§a sprite Ä‘Ã£ liÃªn káº¿t
                        const runtimeIndexA = allTargets.indexOf(linkedSpriteA);
                        const runtimeIndexB = allTargets.indexOf(linkedSpriteB);

                        // So sÃ¡nh 'sá»‘ bÃ¡o danh' Ä‘á»ƒ sáº¯p xáº¿p
                        return runtimeIndexA - runtimeIndexB;
                    });

                    // BÆ¯á»šC 4: Táº O Máº¢NG Káº¾T QUáº¢ CUá»I CÃ™NG
                    console.log("â–¶ï¸ BÆ°á»›c 4: Chuyá»ƒn Ä‘á»•i tá»a Ä‘á»™ vÃ  gÃ¡n index tuáº§n tá»± má»›i...");
                    const finalLayoutIcons = visibleIcons.map((icon, index) => {
                        const pixelCoords = toPixelWithMenu(icon.x, icon.y);
                        return {
                            x: pixelCoords.x,
                            y: pixelCoords.y,
                            index: index // GÃ¡n index tuáº§n tá»± 0, 1, 2... sau khi Ä‘Ã£ sáº¯p xáº¿p
                        };
                    });

                    console.log("âœ… HOÃ€N THÃ€NH! Icons Ä‘Æ°á»£c táº¡o theo logic Hybrid má»›i:");
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
                    console.log("ðŸ” Searching for Skip Intro button...");
                    
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
                                console.log(`âœ… Found Skip Intro target: ${target.sprite.name}`);
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
                                console.log(`âœ… Skip Intro button found at pixel coordinates: (${pixelCoords.x}, ${pixelCoords.y})`);
                                return {
                                    x: pixelCoords.x,
                                    y: pixelCoords.y,
                                    visible: introTarget.visible,
                                    targetName: introTarget.sprite.name,
                                    currentCostume: introTarget.currentCostume,
                                    costumes: introTarget.sprite.costumes.map(c => c.name)
                                };
                            } else {
                                console.warn("âš ï¸ Skip Intro target found but coordinates conversion failed");
                                return null;
                            }
                        } else {
                            console.log("â„¹ï¸ Skip Intro button not found (no target with name 'Intro' and costume 'Skip')");
                            return null;
                        }
                        
                    } catch (error) {
                        console.error("âŒ Error while searching for Skip Intro button:", error);
                        return null;
                    }
                }

                // Find Skip Intro button
                const skipIntroButton = findSkipIntroButton(allTargets);

                // Log the result for debugging
                if (skipIntroButton) {
                    console.log("ðŸŽ¯ Skip Intro Button Detection Result:");
                    console.log(`   - Found: YES`);
                    console.log(`   - Coordinates: (${skipIntroButton.x}, ${skipIntroButton.y})`);
                    console.log(`   - Visible: ${skipIntroButton.visible}`);
                    console.log(`   - Target Name: ${skipIntroButton.targetName}`);
                    console.log(`   - Available Costumes: ${skipIntroButton.costumes.join(', ')}`);
                } else {
                    console.log("ðŸŽ¯ Skip Intro Button Detection Result:");
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

    def find_phase_variable(self, state_data):
        """Find phase variable in state data"""
        try:
            print("ðŸ” Searching for phase variable in state data...")
            
            # Search in global variables first
            variables = state_data.get('variables', {})
            
            # Look for phase variable by name
            for var_key, var_data in variables.items():
                if isinstance(var_data, dict) and var_data.get('name') == 'phase':
                    print(f"âœ… Found phase variable in global variables: {var_data}")
                    return var_data.get('value')
            
            # Search in allTargets variables
            all_targets = state_data.get('allTargets', [])
            for target in all_targets:
                target_vars = target.get('variables', {})
                for var_key, var_data in target_vars.items():
                    if isinstance(var_data, dict) and var_data.get('name') == 'phase':
                        print(f"âœ… Found phase variable in target '{target.get('name', 'Unknown')}': {var_data}")
                        return var_data.get('value')
            
            print("âš ï¸ Phase variable not found in state data")
            return None
            
        except Exception as e:
            print(f"âŒ Error finding phase variable: {e}")
            return None

    def detect_horror_by_phase_comparison(self, before_state, after_state):
        """Detect horror by comparing phase value before and after drag"""
        try:
            print("\nðŸ” PHASE COMPARISON HORROR DETECTION")
            print("="*60)
            
            # Find phase values
            phase_before = self.find_phase_variable(before_state)
            phase_after = self.find_phase_variable(after_state)
            
            print(f"ðŸ“Š Phase value BEFORE drag: {phase_before}")
            print(f"ðŸ“Š Phase value AFTER drag: {phase_after}")
            
            # Convert to string for comparison (handle both string and numeric values)
            phase_before_str = str(phase_before) if phase_before is not None else None
            phase_after_str = str(phase_after) if phase_after is not None else None
            
            if phase_before_str is None or phase_after_str is None:
                print("âŒ Cannot find phase variable - will use screenshot fallback")
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
            
            print(f"\nðŸŽ¯ PHASE COMPARISON RESULT:")
            print(f"   Phase transition: {phase_before_str} -> {phase_after_str}")
            print(f"   Has Horror: {has_horror} (only 1->2 counts as horror)")
            print(f"   Method: {method}")
            print(f"   Confidence: {confidence:.1f}")
            print("="*60)
            
            return result
            
        except Exception as e:
            print(f"âŒ Error in phase comparison: {e}")
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
            print("ðŸ“ Calculating icons area from state data...")
            
            # Check if state_data has icons directly or needs to be extracted
            icons = None
            if 'icons' in state_data:
                icons = state_data['icons']
            elif 'layout' in state_data and 'icons' in state_data['layout']:
                icons = state_data['layout']['icons']
            else:
                print("âŒ No icons data found in state")
                return None
            
            if not icons:
                print("âŒ Icons list is empty")
                return None
                
            # Filter icons 0-18 (exclude icon 19 if exists)
            target_icons = [icon for icon in icons if icon.get('index', 0) <= 18]
            
            if len(target_icons) < 5:
                print(f"âŒ Only {len(target_icons)} target icons available, need at least 5")
                return None
            
            # Find valid coordinates
            valid_icons = [icon for icon in target_icons if icon.get('x') is not None and icon.get('y') is not None]
            
            if len(valid_icons) == 0:
                print("âŒ No icons with valid coordinates found")
                return None
                
            print(f"ðŸ“Š Found {len(valid_icons)} icons with valid coordinates (0-18)")
            
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
            
            print(f"ðŸ“ Icons area (0-18): {area['x']}, {area['y']}, {area['width']}x{area['height']}")
            return area
            
        except Exception as e:
            print(f"âŒ Error calculating icons area: {e}")
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
            print(f"âŒ Error calculating histogram: {e}")
            return {'red': 0, 'green': 0, 'blue': 0, 'brightness': 0, 'saturation': 0}

    def compare_icons_histogram(self, before_img, after_img):
        """Histogram comparison for horror detection"""
        try:
            print("ðŸŽ¨ Analyzing color histogram changes...")
            
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
            print(f"âŒ Error in histogram comparison: {e}")
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
            print("ðŸ” Analyzing structural similarity (SSIM)...")
            
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
            print(f"âŒ Error in SSIM comparison: {e}")
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
            print(f"âŒ Error in simple SSIM: {e}")
            return 0.5

    def detect_horror_icons_area(self, before_img, after_img):
        """Histogram + SSIM Combo for horror detection"""
        try:
            print("\nðŸ” VISUAL HORROR DETECTION - Histogram + SSIM Combo")
            print("="*60)
            
            if before_img is None or after_img is None:
                print("âŒ Missing screenshot data")
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
            
            print(f"\nðŸŽ¯ HISTOGRAM + SSIM COMBO RESULT:")
            print(f"   Color Horror Score: {color_result['horror_score']:.3f} (threshold: 0.25)")
            print(f"   Structural Change: {structural_result['difference_score']:.3f} (threshold: 0.30)")  
            print(f"   Has Horror: {has_horror} (method: {method})")
            print(f"   Confidence: {confidence:.3f}")
            print("="*60)
            
            return result
            
        except Exception as e:
            print(f"âŒ Error in horror detection: {e}")
            import traceback
            traceback.print_exc()
            return {'has_horror': False, 'confidence': 0, 'method': 'error', 'reason': str(e)}

    def calculate_extended_screenshot_area(self, state_data):
        """Calculate extended area from (0,0) to icon index 18 coordinates"""
        try:
            print("ðŸ“ Calculating extended screenshot area from (0,0) to icon 18...")
            
            # Check if state_data has icons directly or needs to be extracted
            icons = None
            if 'icons' in state_data:
                icons = state_data['icons']
            elif 'layout' in state_data and 'icons' in state_data['layout']:
                icons = state_data['layout']['icons']
            else:
                print("âŒ No icons data found in state")
                return None
            
            if not icons:
                print("âŒ Icons list is empty")
                return None
                
            # Find icon index 18 (highest icon before icon 19)
            icon_18 = None
            for icon in icons:
                if icon.get('index') == 18:
                    icon_18 = icon
                    break
            
            if not icon_18 or icon_18.get('x') is None or icon_18.get('y') is None:
                print("âŒ Icon index 18 not found or has invalid coordinates")
                return None
            
            print(f"ðŸ“Š Found icon 18 at coordinates: ({icon_18['x']}, {icon_18['y']})")
            
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
            
            print(f"ðŸ“ Extended screenshot area: {area['x']}, {area['y']}, {area['width']}x{area['height']}")
            print(f"   Coverage: (0,0) to icon 18 at ({icon_18['x']}, {icon_18['y']})")
            return area
            
        except Exception as e:
            print(f"âŒ Error calculating extended screenshot area: {e}")
            return None

    def compare_icons_histogram_extended(self, before_img, after_img):
        """Enhanced histogram comparison for extended area with dynamic background tolerance"""
        try:
            print("ðŸŽ¨ Analyzing color histogram changes (Extended Area)...")
            
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
            print(f"âŒ Error in extended histogram comparison: {e}")
            return {'has_horror': False, 'horror_score': 0, 'red_shift': 0, 'brightness_change': 0, 'saturation_change': 0, 'threshold_used': 0.40}

    def compare_icons_ssim_extended(self, before_img, after_img):
        """Enhanced SSIM comparison for extended area with dynamic background tolerance"""
        try:
            print("ðŸ” Analyzing structural similarity (SSIM - Extended Area)...")
            
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
            print(f"âŒ Error in extended SSIM comparison: {e}")
            return {'has_horror': False, 'similarity_score': 1.0, 'difference_score': 0.0, 'threshold_used': 0.45}

    def detect_horror_extended_area(self, before_img, after_img):
        """Enhanced horror detection for extended area (0,0) to icon 18 with background tolerance"""
        try:
            print("\nðŸ” EXTENDED AREA HORROR DETECTION - Enhanced for Dynamic Background")
            print("="*70)
            print("Coverage: (0,0) to icon 18 - Adjusted thresholds for background movement")
            print("="*70)
            
            if before_img is None or after_img is None:
                print("âŒ Missing screenshot data")
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
            
            print(f"\nðŸŽ¯ EXTENDED AREA DETECTION RESULT:")
            print(f"   Color Horror Score: {color_result['horror_score']:.3f} (threshold: {color_result['threshold_used']})")
            print(f"   Structural Change: {structural_result['difference_score']:.3f} (threshold: {structural_result['threshold_used']})")  
            print(f"   Has Horror: {has_horror} (method: {method})")
            print(f"   High Confidence: {high_confidence_horror}")
            print(f"   Final Confidence: {confidence:.3f}")
            print(f"   Area Coverage: (0,0) to icon 18 position")
            print("="*70)
            
            return result
            
        except Exception as e:
            print(f"âŒ Error in extended area horror detection: {e}")
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
            print("ðŸ§¹ Complete cleanup: Closing Chrome debug and cleaning resources...")
            
            for title_part in ['cocrea.world', 'Chrome']:
                windows = gw.getWindowsWithTitle(title_part)
                for window in windows:
                    try:
                        print(f"   ðŸ—‚ï¸ Closing window: {window.title}")
                        window.close()
                    except:
                        pass
            
            time.sleep(2)
            
            if hasattr(self, 'chrome_process') and self.chrome_process:
                try:
                    print("   ðŸ”„ Terminating Chrome process...")
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
                                print(f"   âš”ï¸ Killing leftover Chrome debug process: {proc.info['pid']}")
                                proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except ImportError:
                print("   âš ï¸ psutil not available for process cleanup")
            
            self.ws_url = None
            
            print("âœ… Complete cleanup finished - Chrome debug fully closed")
            time.sleep(1)
            return True
            
        except Exception as e:
            print(f"âŒ Complete cleanup error: {e}")
            return False

    def navigate_and_prepare_game(self, url):
        try:
            print(f"ðŸŒ Opening game URL with fresh Chrome debug session: {url}")
            
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
                messagebox.showerror("Extension Not Found", "uBlock Origin not found in 'extensions' folder.")
                return False
            
            print(f"âœ… Found AdBlocker: {ublock_path}")
            
            chrome_exe = None
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe", 
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
            ]
            for path in chrome_paths:
                if os.path.exists(path): 
                    chrome_exe = path
                    break
                    
            if not chrome_exe: 
                messagebox.showerror("Error", "Google Chrome not found.")
                return False
                
            chrome_cmd = [
                chrome_exe, 
                f"--remote-debugging-port=9222", 
                f"--user-data-dir={os.path.join(os.getcwd(), 'chrome_debug_profile')}",
                "--no-first-run", 
                "--window-size=1280,720", 
                "--remote-allow-origins=*",
                f"--load-extension={ublock_path}", 
                url
            ]
            
            print("ðŸ”§ Launching fresh Chrome with AdBlocker and DevTools...")
            self.chrome_process = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            print("â³ Waiting 20s for fresh Chrome and AdBlocker to initialize...")
            time.sleep(20)
            
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
                messagebox.showerror("Setup Error", "Could not find the game window.")
                return False
                
            print(f"ðŸŽ¯ Found fresh game window: '{game_window.title}'. Activating...")
            game_window.activate()
            time.sleep(0.5)
            game_window.maximize()
            time.sleep(2)
            
            detector = ImageDetector("cocrea.world")
            run_button_clicked = False
            max_retries = 3
            
            for attempt in range(1, max_retries + 1):
                print(f"\n--- ðŸš€ Finding 'Run' Button - Attempt {attempt}/{max_retries} ---")
                
                if attempt > 1:
                    print("   -> Action: Reloading page to clear potential issues...")
                    pyautogui.hotkey('ctrl', 'r')
                    time.sleep(2)
                    
                wait_duration = 40
                print(f"   -> Waiting {wait_duration}s for page content to load...")
                time.sleep(wait_duration)
                
                print("   -> Searching for 'Run' button on screen...")
                if detector.click_run_button(max_pagedown_attempts=5):
                    print(f"âœ… SUCCESS: 'Run' button found and clicked on attempt {attempt}.")
                    run_button_clicked = True
                    break
                else:
                    print(f"âš ï¸ FAILED: 'Run' button not found on attempt {attempt}.")
                    if attempt < max_retries:
                        print("   -> Preparing for next attempt...")
                    else:
                        messagebox.showerror("Setup Error", f"Could not find 'Run' button after {max_retries} attempts.")
                        return False
                        
            if not run_button_clicked: 
                return False
                
            game_load_wait = 10
            print(f"\nâ³ Waiting {game_load_wait}s for game to load after clicking Run...")
            time.sleep(game_load_wait)
            
            if detector.click_fullscreen_button(wait_time=8):
                print("âœ… Clicked game's own fullscreen button.")
                time.sleep(2)
            else:
                print("âš ï¸ Fullscreen button not found, continuing anyway.")
                
            print("âŒ¨ï¸ Pressing F11 for true fullscreen mode...")
            pyautogui.press('f11')
            time.sleep(3)
            
            print("âœ… Fresh game setup completed. Ready for HYBRID JSON generation.")
            return True
            
        except Exception as e:
            messagebox.showerror("Setup Error", f"Failed to prepare game: {e}")
            print(f"âŒ Failed to navigate and prepare game: {e}")
            self.cleanup_chrome_complete()
            return False

    def _get_websocket_url(self):
        if self.ws_url: return self.ws_url
        try:
            response = requests.get("http://localhost:9222/json", timeout=5)
            tabs = response.json()
            target_tab = next((t for t in tabs if t.get('type') == 'page' and 'cocrea.world' in t.get('url', '')), None)
            if not target_tab: 
                messagebox.showerror("CDP Error", "Could not find cocrea.world tab.")
                return None
            self.ws_url = target_tab['webSocketDebuggerUrl']
            return self.ws_url
        except Exception as e:
            print(f"âŒ Could not get WebSocket URL: {e}")
            return None

    def generate_complete_json(self, url, output_path=None):
        """
        Simplified Complete Logic: A vs B vs C
        
        1. Capture A (screenshot + phase) - INITIAL STATE
        2. Drag icon 19 â†’ Capture B (screenshot + phase) - AFTER ICON 19
           - Compare phase A vs B â†’ if hasHorror = True â†’ STOP
           - Compare screenshot A vs B â†’ if hasHorror = True â†’ STOP
        3. Find highest index icon â†’ Drag â†’ Capture C (screenshot + phase) - AFTER HIGHEST INDEX
           - Compare phase A vs C â†’ if hasHorror = True â†’ STOP  
           - Compare screenshot A vs C â†’ if hasHorror = True â†’ STOP
        4. Final result = False
        
        STOP immediately when hasHorror = True is detected
        """
        try:
            print("\nðŸš€ Starting SIMPLIFIED COMPLETE LOGIC (A vs B vs C)")
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
            
            print("ðŸ§ª Testing JavaScript environment...")
            test_response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": "window.location.href", "returnByValue": True}
            })
            if test_response is None or 'result' not in test_response:
                print("âŒ JavaScript environment test failed")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
            print("âœ… JavaScript environment ready")
            
            print("ðŸ–±ï¸ Skipping intros via CDP...")
            # ============================
            # MULTI-TARGET SKIP INTRO: Click ALL Skip Intro buttons + Blind Clicks
            # ============================
            print("\nðŸ” MULTI-TARGET SKIP INTRO: Finding and clicking ALL Skip Intro buttons...")

            # Phase 1: Find ALL Skip Intro buttons
            print("ðŸ“ Phase 1: Searching for ALL Skip Intro buttons...")

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
                    
                    print(f"âœ… Found {button_count} Skip Intro buttons:")
                    print(f"   ðŸ“Š Visible: {visible_count}, Hidden: {hidden_count}")
                    
                    # Click ALL buttons found
                    for i, button in enumerate(all_buttons):
                        button_num = i + 1
                        x, y = button['x'], button['y']
                        visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                        costume_name = button.get('currentCostumeName', 'Unknown')
                        
                        print(f"ðŸŽ¯ Clicking button {button_num}/{button_count}: ({x}, {y}) - {visible_status} - {costume_name}")
                        
                        # Click this specific button
                        self._simulate_click_via_cdp(ws, x, y)
                        time.sleep(0.8)  # Wait between clicks
                        
                        smart_clicks_performed += 1
                    
                    print(f"âœ… Smart clicks completed: {smart_clicks_performed} buttons clicked!")
                    
                else:
                    reason = skip_result.get('reason', 'unknown') if skip_result else 'no_result'
                    print(f"â„¹ï¸ No Skip Intro buttons found - Reason: {reason}")
                    print("ðŸ”„ Will use blind click method only...")

            # Phase 2: ALWAYS perform blind clicks for additional assurance
            if smart_clicks_performed > 0:
                print("ðŸ”„ Phase 2: Performing ADDITIONAL blind clicks for triple protection...")
                print("   â†’ This ensures intro skipping even if smart clicks had edge cases")
            else:
                print("ðŸ–±ï¸ Phase 2: Performing STANDARD blind clicks (no smart buttons found)...")

            # Perform blind clicks regardless of smart click success
            for i in range(self.intro_skip_clicks):  # Use config value
                self._simulate_click_via_cdp(ws, 960, 540)
                time.sleep(0.5)

            # Summary
            if smart_clicks_performed > 0:
                print("ðŸŽ‰ MULTI-TARGET SKIP INTRO COMPLETED:")
                print(f"   âœ… Smart clicks: {smart_clicks_performed} buttons clicked successfully")
                print(f"   âœ… Button details: {len(all_buttons)} total found")
                for i, button in enumerate(all_buttons):
                    visible_status = "VISIBLE" if button['visible'] else "HIDDEN"
                    print(f"      {i+1}. ({button['x']}, {button['y']}) - {visible_status}")
                print(f"   âœ… Blind clicks: {self.intro_skip_clicks} additional clicks performed")
                print("   ðŸ›¡ï¸ Triple protection ensures maximum skip success rate")
            else:
                print("ðŸŽ‰ STANDARD SKIP INTRO COMPLETED:")
                print("   âš ï¸ Smart clicks: N/A (No Skip Intro buttons found)")
                print(f"   âœ… Blind clicks: {self.intro_skip_clicks} clicks performed")
                print("   ðŸ”„ Standard method used as fallback")

            print("ðŸŽ® Skip intro phase finished - Game should be ready for interaction")
            
            # Initialize final result variables
            final_has_horror = False
            final_confidence = 0
            final_detection_method = "no_horror_detected"
            final_visual_result = {}
            
            # ============================
            # CAPTURE A: Initial State (Screenshot + Phase)
            # ============================
            print("\n" + "="*70)
            print("ðŸ“¸ CAPTURE A: Initial State (Screenshot + Phase)")
            print("="*70)
            
            print("ðŸ“Š Extracting initial state A + coordinates...")
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": self.js_get_state_and_coordinates, "awaitPromise": True, "returnByValue": True}
            })
            
            if response is None or 'result' not in response:
                print("âŒ Failed to get initial state A")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            state_A = response.get('result', {}).get('result', {}).get('value')
            if not state_A or "error" in state_A: 
                print(f"âŒ Failed to get initial state A: {state_A.get('error', 'Unknown') if state_A else 'None'}")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
            
            phase_A = self.find_phase_variable(state_A)
            print(f"âœ… State A captured - Phase A: {phase_A}")
            
            # ============================
            # CRITICAL CHECK: Verify icon count before proceeding
            # ============================
            icons_count = len(state_A.get('icons', []))
            print(f"\nðŸ“Š ICON COUNT VERIFICATION: Found {icons_count} icons")
            
            if icons_count < 20:
                print(f"âš ï¸ INSUFFICIENT ICONS: Only {icons_count} icons found (need 20+ for icon index 19)")
                print("ðŸ”š EARLY EXIT: Setting hasHorror = False due to insufficient icons")
                
                # Get final layout for JSON generation
                print("ðŸ“Š Extracting final full layout...")
                response = self._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": self.js_full_layout, "awaitPromise": True, "returnByValue": True}
                })
                
                if response and 'result' in response:
                    layout_state = response.get('result', {}).get('result', {}).get('value')
                    if layout_state and "error" not in layout_state:
                        print("âœ… Final full layout captured successfully")
                        
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
                        
                        print("ðŸ’¾ Saving JSON file...")
                        json_file_path = self.save_json_file(final_json, output_path, custom_title=page_title)
                        
                        if json_file_path:
                            print(f"\nðŸŽ‰ EARLY EXIT COMPLETED - INSUFFICIENT ICONS!")
                            print("="*70)
                            print(f"âœ… hasHorror Detection: False (insufficient icons)")
                            print(f"âœ… Icons Found: {icons_count}/20")
                            print(f"âœ… Reason: Need icon index 19 for horror detection")
                            print("="*70)
                            
                            self.cleanup_chrome_complete()
                            return True, json_file_path, final_json
                        else:
                            print("âŒ Failed to save JSON file")
                            ws.close()
                            self.cleanup_chrome_complete()
                            return False, None, None
                    else:
                        print("âŒ Failed to get final layout for insufficient icons case")
                        ws.close()
                        self.cleanup_chrome_complete()
                        return False, None, None
                else:
                    print("âŒ Failed to execute JS for final layout")
                    ws.close()
                    self.cleanup_chrome_complete()
                    return False, None, None
            else:
                print(f"âœ… SUFFICIENT ICONS: {icons_count} icons found - proceeding with horror detection")
            
            # Capture screenshot A
            extended_area = self.calculate_extended_screenshot_area(state_A)
            screenshot_A = None
            if extended_area:
                screenshot_A = self.capture_icons_area_screenshot(extended_area)
                if screenshot_A is not None:
                    print(f"âœ… Screenshot A captured - Size: {screenshot_A.shape}")
                else:
                    print("âš ï¸ Failed to capture screenshot A")
            else:
                print("âš ï¸ Could not calculate extended area for screenshot A")
            
            # ============================
            # DRAG ICON 19 & CAPTURE B: After Icon 19 (Screenshot + Phase)
            # ============================
            print("\n" + "="*70)
            print("ðŸ–±ï¸ DRAG ICON 19 & CAPTURE B: After Icon 19 (Screenshot + Phase)")
            print("="*70)
            
            drag_coords = state_A.get('dragCoordinates', {})
            icon_coords = drag_coords.get('icon')
            slot_coords = drag_coords.get('slot')
            
            # --- ENHANCED DEBUG LOGGING FOR DRAG COORDINATES ---
            # Log the retrieved coordinates for inspection
            print("\nðŸ”¬ DEBUG: Checking Drag Coordinates from State A...")
            print(f"   - Raw icon_coords (icon 19): {icon_coords}")
            print(f"   - Raw slot_coords (slot 3): {slot_coords}")

            # Detailed validation
            is_icon_valid = icon_coords and icon_coords.get('x') is not None and icon_coords.get('y') is not None
            is_slot_valid = slot_coords and slot_coords.get('x') is not None and slot_coords.get('y') is not None
            
            if not is_icon_valid or not is_slot_valid:
                print("âŒ FATAL: Missing or invalid drag coordinates. Cannot proceed with horror detection.")
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

            print(f"ðŸŽ¯ Dragging icon 19 from ({icon_coords['x']}, {icon_coords['y']}) to ({slot_coords['x']}, {slot_coords['y']})")
            self._simulate_drag_via_cdp(ws, icon_coords['x'], icon_coords['y'], slot_coords['x'], slot_coords['y'])
            
            wait_time = 25
            print(f"â³ Waiting {wait_time} seconds for transition...")
            time.sleep(wait_time)
            
            print("ðŸ“Š Extracting state B after icon 19...")
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": self.js_get_state_and_coordinates, "awaitPromise": True, "returnByValue": True}
            })
            
            if response is None or 'result' not in response:
                print("âŒ Failed to get state B")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            state_B = response.get('result', {}).get('result', {}).get('value')
            if not state_B or "error" in state_B: 
                print(f"âŒ Failed to get state B: {state_B.get('error', 'Unknown') if state_B else 'None'}")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
            
            phase_B = self.find_phase_variable(state_B)
            print(f"âœ… State B captured - Phase B: {phase_B}")
            
            # Capture screenshot B
            screenshot_B = None
            if extended_area:
                screenshot_B = self.capture_icons_area_screenshot(extended_area)
                if screenshot_B is not None:
                    print(f"âœ… Screenshot B captured - Size: {screenshot_B.shape}")
                else:
                    print("âš ï¸ Failed to capture screenshot B")
            
            # ============================
            # COMPARE A vs B: Phase Comparison
            # ============================
            print("\n" + "="*70)
            print("ðŸ” COMPARE A vs B: Phase Comparison")
            print("="*70)
            
            print(f"ðŸ“Š Comparing Phase A ({phase_A}) vs Phase B ({phase_B})")
            
            # Phase comparison logic
            phase_A_str = str(phase_A) if phase_A is not None else None
            phase_B_str = str(phase_B) if phase_B is not None else None
            
            # Initialize flags for this comparison round
            horror_detected_phase_AB = False
            
            if phase_A_str is not None and phase_B_str is not None:
                has_horror_phase_AB = (phase_A_str == "1" and phase_B_str == "2")
                
                if has_horror_phase_AB:
                    print(f"ðŸŽ‰ SUCCESS! Phase A vs B: {phase_A_str} â†’ {phase_B_str} = hasHorror TRUE")
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
                    print("ðŸ›‘ STOP: Horror detected via phase A vs B")
                else:
                    print(f"â„¹ï¸ No horror in phase A vs B: {phase_A_str} â†’ {phase_B_str}")
            else:
                print("âš ï¸ Cannot compare phases A vs B (missing phase data)")
                print("â„¹ï¸ Will proceed to screenshot comparison")
            
            # ============================
            # COMPARE A vs B: Screenshot Comparison (ALWAYS TRY)
            # ============================
            if not horror_detected_phase_AB:  # Only if phase didn't detect horror
                print("\n" + "="*50)
                print("ðŸ“¸ COMPARE A vs B: Screenshot Comparison")
                print("="*50)
                
                if screenshot_A is not None and screenshot_B is not None:
                    print("ðŸ” Comparing Screenshot A vs Screenshot B...")
                    visual_result_AB = self.detect_horror_extended_area(screenshot_A, screenshot_B)
                    
                    if visual_result_AB and visual_result_AB['has_horror']:
                        print(f"ðŸŽ‰ SUCCESS! Screenshot A vs B detected hasHorror TRUE")
                        final_has_horror = True
                        final_confidence = visual_result_AB['confidence']
                        final_detection_method = f"screenshot_A_vs_B_{visual_result_AB['method']}_icon_19"
                        final_visual_result = visual_result_AB
                        final_visual_result['horror_icon_index'] = 19
                        final_visual_result['comparison'] = 'A_vs_B'
                        # STOP - Horror detected!
                        print("ðŸ›‘ STOP: Horror detected via screenshot A vs B")
                    else:
                        print("â„¹ï¸ No horror in screenshot A vs B")
                else:
                    print("âš ï¸ Cannot compare screenshots A vs B (missing screenshot data)")
            
            # ============================
            # IF NO HORROR YET: Try Highest Index Icon
            # ============================
            if not final_has_horror:
                print("\n" + "="*70)
                print("ðŸ” NO HORROR YET: Try Highest Index Icon")
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
                            
                            print(f"ðŸŽ¯ Found highest index icon: {highest_index}")
                            
                            available_slots = current_layout_data.get('layout', {}).get('slots', [])
                            slot_4_coords = next((slot for slot in available_slots if slot.get('index') == 4), None)
                            
                            if slot_4_coords and slot_4_coords.get('x') is not None:
                                # ============================
                                # DRAG HIGHEST INDEX & CAPTURE C: After Highest Index (Screenshot + Phase)
                                # ============================
                                print("\n" + "="*70)
                                print(f"ðŸ–±ï¸ DRAG ICON {highest_index} & CAPTURE C: After Highest Index (Screenshot + Phase)")
                                print("="*70)
                                
                                print(f"ðŸŽ¯ Dragging icon {highest_index} from ({highest_index_icon['x']}, {highest_index_icon['y']}) to ({slot_4_coords['x']}, {slot_4_coords['y']})")
                                self._simulate_drag_via_cdp(
                                    ws, 
                                    highest_index_icon['x'], 
                                    highest_index_icon['y'], 
                                    slot_4_coords['x'], 
                                    slot_4_coords['y']
                                )
                                
                                wait_time = 25
                                print(f"â³ Waiting {wait_time}s for transition...")
                                time.sleep(wait_time)
                                
                                print("ðŸ“Š Extracting state C after highest index...")
                                response = self._send_cdp_command(ws, {
                                    "method": "Runtime.evaluate", 
                                    "params": {"expression": self.js_get_state_and_coordinates, "awaitPromise": True, "returnByValue": True}
                                })
                                
                                if response and 'result' in response:
                                    state_C = response.get('result', {}).get('result', {}).get('value')
                                    
                                    if state_C:
                                        phase_C = self.find_phase_variable(state_C)
                                        print(f"âœ… State C captured - Phase C: {phase_C}")
                                        
                                        # Capture screenshot C
                                        screenshot_C = None
                                        if extended_area:
                                            screenshot_C = self.capture_icons_area_screenshot(extended_area)
                                            if screenshot_C is not None:
                                                print(f"âœ… Screenshot C captured - Size: {screenshot_C.shape}")
                                            else:
                                                print("âš ï¸ Failed to capture screenshot C")
                                        
                                        # Initialize flags for A vs C comparison
                                        horror_detected_phase_AC = False
                                        
                                        # ============================
                                        # COMPARE A vs C: Phase Comparison
                                        # ============================
                                        print("\n" + "="*70)
                                        print("ðŸ” COMPARE A vs C: Phase Comparison")
                                        print("="*70)
                                        
                                        print(f"ðŸ“Š Comparing Phase A ({phase_A}) vs Phase C ({phase_C})")
                                        
                                        phase_C_str = str(phase_C) if phase_C is not None else None
                                        
                                        if phase_A_str is not None and phase_C_str is not None:
                                            has_horror_phase_AC = (phase_A_str == "1" and phase_C_str == "2")
                                            
                                            if has_horror_phase_AC:
                                                print(f"ðŸŽ‰ SUCCESS! Phase A vs C: {phase_A_str} â†’ {phase_C_str} = hasHorror TRUE")
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
                                                print("ðŸ›‘ STOP: Horror detected via phase A vs C")
                                            else:
                                                print(f"â„¹ï¸ No horror in phase A vs C: {phase_A_str} â†’ {phase_C_str}")
                                        else:
                                            print("âš ï¸ Cannot compare phases A vs C (missing phase data)")
                                            print("â„¹ï¸ Will proceed to screenshot comparison")
                                        
                                        # ============================
                                        # COMPARE A vs C: Screenshot Comparison (ALWAYS TRY)
                                        # ============================
                                        if not horror_detected_phase_AC:  # Only if phase didn't detect horror
                                            print("\n" + "="*50)
                                            print("ðŸ“¸ COMPARE A vs C: Screenshot Comparison")
                                            print("="*50)
                                            
                                            if screenshot_A is not None and screenshot_C is not None:
                                                print("ðŸ” Comparing Screenshot A vs Screenshot C...")
                                                visual_result_AC = self.detect_horror_extended_area(screenshot_A, screenshot_C)
                                                
                                                if visual_result_AC and visual_result_AC['has_horror']:
                                                    print(f"ðŸŽ‰ SUCCESS! Screenshot A vs C detected hasHorror TRUE")
                                                    final_has_horror = True
                                                    final_confidence = visual_result_AC['confidence']
                                                    final_detection_method = f"screenshot_A_vs_C_{visual_result_AC['method']}_icon_{highest_index}"
                                                    final_visual_result = visual_result_AC
                                                    final_visual_result['horror_icon_index'] = highest_index
                                                    final_visual_result['comparison'] = 'A_vs_C'
                                                    # STOP - Horror detected!
                                                    print("ðŸ›‘ STOP: Horror detected via screenshot A vs C")
                                                else:
                                                    print("â„¹ï¸ No horror in screenshot A vs C")
                                                    print("ðŸ”š FINAL RESULT: hasHorror = False (all methods exhausted)")
                                                    final_detection_method = "all_methods_exhausted"
                                            else:
                                                print("âš ï¸ Cannot compare screenshots A vs C (missing screenshot data)")
                                                print("ðŸ”š FINAL RESULT: hasHorror = False (screenshot A vs C unavailable)")
                                                final_detection_method = "screenshot_A_vs_C_unavailable"
                                    else:
                                        print("âŒ Failed to get state C")
                                        print("ðŸ”š FINAL RESULT: hasHorror = False (failed to capture state C)")
                                        final_detection_method = "failed_state_C"
                                else:
                                    print("âŒ Failed to execute JS for state C")
                                    print("ðŸ”š FINAL RESULT: hasHorror = False (JS execution failed)")
                                    final_detection_method = "js_execution_failed"
                            else:
                                print("âŒ Slot 4 coordinates not found")
                                print("ðŸ”š FINAL RESULT: hasHorror = False (slot 4 unavailable)")
                                final_detection_method = "slot_4_unavailable"
                        else:
                            print("â„¹ï¸ No icons with index > 19 available")
                            print("ðŸ”š FINAL RESULT: hasHorror = False (no high index icons)")
                            final_detection_method = "no_high_index_icons"
                    else:
                        print("âŒ Failed to get current layout data")
                        print("ðŸ”š FINAL RESULT: hasHorror = False (layout data unavailable)")
                        final_detection_method = "layout_data_unavailable"
                else:
                    print("âŒ Failed to fetch current layout")
                    print("ðŸ”š FINAL RESULT: hasHorror = False (layout fetch failed)")
                    final_detection_method = "layout_fetch_failed"
            
            # ============================
            # FINAL RESULT SUMMARY
            # ============================
            print("\n" + "="*70)
            print("ðŸŽ¯ FINAL DETECTION RESULT SUMMARY")
            print("="*70)
            print(f"hasHorror: {final_has_horror}")
            print(f"Method: {final_detection_method}")
            print(f"Confidence: {final_confidence:.3f}")
            if 'horror_icon_index' in final_visual_result:
                print(f"Horror Icon Index: {final_visual_result['horror_icon_index']}")
            if 'comparison' in final_visual_result:
                print(f"Comparison: {final_visual_result['comparison']}")
            if 'phase_before' in final_visual_result and 'phase_after' in final_visual_result:
                print(f"Phase Transition: {final_visual_result['phase_before']} â†’ {final_visual_result['phase_after']}")
            print("="*70)
            
            # ============================
            # Full Layout Capture
            # ============================
            print("\n" + "="*70)
            print("ðŸ“Š Full Layout Capture")
            print("="*70)
            
            print("ðŸ“Š Extracting final full layout...")
            response = self._send_cdp_command(ws, {
                "method": "Runtime.evaluate", 
                "params": {"expression": self.js_full_layout, "awaitPromise": True, "returnByValue": True}
            })
            
            if response is None or 'result' not in response:
                print("âŒ Failed to get final full layout")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            layout_state = response.get('result', {}).get('result', {}).get('value')
            if not layout_state or "error" in layout_state: 
                print(f"âŒ Failed to get final layout: {layout_state.get('error', 'Unknown') if layout_state else 'None'}")
                ws.close()
                self.cleanup_chrome_complete()
                return False, None, None
                
            print("âœ… Final full layout captured successfully")
            
            ws.close()
            
            # ============================
            # Generate Final JSON
            # ============================
            print("\n" + "="*70)
            print("ðŸ“ Generate Final JSON")
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
                        "logic_flow": "A(initial) â†’ B(icon19) â†’ C(highest) with early stop"
                    }
                },
                "layout": layout_state.get('layout', {}),
                "settings": {
                    "aspectRatio": "96:83"
                }
            }
            
            print("ðŸ’¾ Saving JSON file...")
            json_file_path = self.save_json_file(final_json, output_path, custom_title=page_title)
            if not json_file_path: 
                self.cleanup_chrome_complete()
                return False, None, None
                
            print("\nðŸŽ‰ SIMPLIFIED COMPLETE LOGIC COMPLETED!")
            print("="*70)
            print(f"âœ… hasHorror Detection: {final_has_horror}")
            print(f"âœ… Detection Method: {final_detection_method}")
            print(f"âœ… Logic Flow: A(initial) vs B(icon19) vs C(highest) with early stop")
            if 'comparison' in final_visual_result:
                print(f"âœ… Successful Comparison: {final_visual_result['comparison']}")
            print("="*70)
            
            print("ðŸ§¹ Performing complete cleanup...")
            self.cleanup_chrome_complete()
                
            return True, json_file_path, final_json
            
        except Exception as e:
            print(f"âŒ Simplified complete logic error: {e}")
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
            # Láº¥y json_output_directory tá»« config
            json_output_dir = self.config.get('output', {}).get('json_output_directory', r"I:\VS-Project\Sprunki\JSON")
            os.makedirs(json_output_dir, exist_ok=True)

            if not output_path:
                import re
                
                if custom_title and custom_title.strip():
                    base_name = custom_title.strip()
                    print(f"ðŸ·ï¸ Using custom title for filename: '{base_name}'")
                else:
                    from urllib.parse import urlparse
                    slug_base = urlparse(self.game_url).path.split('/')[-1]
                    if not slug_base: 
                        slug_base = urlparse(self.game_url).netloc.replace('www.','')
                    base_name = slug_base
                    print(f"ðŸ·ï¸ Using URL slug for filename: '{base_name}'")
                
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
            print(f"ðŸ’¾ JSON saved: {output_path}")
            return output_path
        except Exception as e:
            print(f"âŒ Save JSON error: {e}")
            import traceback
            traceback.print_exc()
            return None
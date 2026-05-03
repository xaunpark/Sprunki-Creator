"""
detect_game_panels.py
---------------------
Script chẩn đoán: Phát hiện TẤT CẢ các sprite/button có thể bấm được
trong game Sprunki sau khi click Run hoặc Re-Run.

Cách dùng:
1. Mở Chrome với game đang chạy (debug port 9222)
2. Chạy script này: python detect_game_panels.py
3. Đọc output để biết tên sprite, costume, tọa độ của từng button

Để chạy sau Re-Run: click Re-Run trên game, đợi game load xong (~3-5s), rồi chạy script.
"""

import json
import time
import requests
import websocket

DEBUG_PORT = 9222

# =============================================================================
# JavaScript: Quét TOÀN BỘ targets trong Scratch VM
# =============================================================================
JS_SCAN_ALL_TARGETS = """
new Promise((resolve) => {
    // --- Bước 1: Tìm VM (giống logic js_find_skip_intro) ---
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

    // --- Bước 2: Hàm convert tọa độ logic → pixel ---
    function toPixel(logicX, logicY) {
        const stageW = 640, stageH = 360, screenW = 1920, screenH = 1080, menuWidth = 78;
        const viewportW = screenW - menuWidth;
        const scale = Math.min(viewportW / stageW, screenH / stageH);
        const canvasW = stageW * scale, canvasH = stageH * scale;
        const canvasCenterX = menuWidth + canvasW / 2;
        const canvasCenterY = (screenH - canvasH) / 2 + canvasH / 2;
        return {
            x: Math.round(canvasCenterX + logicX * scale),
            y: Math.round(canvasCenterY - logicY * scale)
        };
    }

    const vm = findVM();
    if (!vm) {
        resolve({ success: false, reason: 'vm_not_found', targets: [], domButtons: [] });
        return;
    }

    const targets = vm.runtime.targets;

    // --- Bước 3: Map toàn bộ targets ---
    const allTargets = targets.map((t, i) => {
        const costumes = t.sprite ? t.sprite.costumes.map(c => c.name) : [];
        const currentCostume = (t.sprite && t.sprite.costumes[t.currentCostume])
            ? t.sprite.costumes[t.currentCostume].name
            : 'unknown';
        const pixel = toPixel(t.x, t.y);
        return {
            index: i,
            name: t.sprite ? t.sprite.name : '(stage)',
            isStage: t.isStage || false,
            visible: t.visible,
            x_logic: Math.round(t.x),
            y_logic: Math.round(t.y),
            x_pixel: pixel.x,
            y_pixel: pixel.y,
            currentCostume: currentCostume,
            costumes: costumes,
            size: t.size || 100,
        };
    });

    // --- Bước 4: Lọc candidates có khả năng là button/panel ---
    // Dấu hiệu nhận biết: visible=true, không phải stage, tên/costume có từ khoá gợi ý
    const buttonKeywords = [
        'play', 'start', 'begin', 'intro', 'skip', 'click', 'press',
        'button', 'btn', 'enter', 'continue', 'next', 'ok', 'yes',
        'panel', 'menu', 'screen', 'overlay', 'popup',
        'bắt đầu', 'chơi'
    ];

    function hasKeyword(str) {
        if (!str) return false;
        const lower = str.toLowerCase();
        return buttonKeywords.some(kw => lower.includes(kw));
    }

    const likelyCandidates = allTargets.filter(t => {
        if (t.isStage) return false;
        if (!t.visible) return false;
        return hasKeyword(t.name) || hasKeyword(t.currentCostume);
    });

    // Visible non-stage targets (để quan sát toàn bộ)
    const visibleNonStage = allTargets.filter(t => !t.isStage && t.visible);

    // --- Bước 5: Quét DOM để tìm nút HTML overlay (nếu có) ---
    const domButtons = [];
    try {
        const domCandidates = document.querySelectorAll(
            'button, [role="button"], input[type="button"], input[type="submit"], [onclick], .btn'
        );
        domCandidates.forEach((el, i) => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return; // ẩn
            domButtons.push({
                index: i,
                tag: el.tagName,
                text: (el.innerText || el.value || el.title || '').trim().substring(0, 80),
                className: el.className.toString().substring(0, 100),
                x_pixel: Math.round(rect.left + rect.width / 2),
                y_pixel: Math.round(rect.top + rect.height / 2),
                visible: rect.width > 0 && rect.height > 0
            });
        });
    } catch(domErr) {
        domButtons.push({ error: domErr.toString() });
    }

    resolve({
        success: true,
        totalTargets: allTargets.length,
        visibleNonStage: visibleNonStage,
        likelyCandidates: likelyCandidates,
        allTargets: allTargets,
        domButtons: domButtons
    });
});
"""


# =============================================================================
# Helper: Kết nối WebSocket tới Chrome CDP
# =============================================================================
def get_websocket_url(port=9222):
    try:
        resp = requests.get(f"http://localhost:{port}/json", timeout=5)
        tabs = resp.json()
        # Ưu tiên tab đang active có URL game
        for tab in tabs:
            if tab.get('type') == 'page' and 'webSocketDebuggerUrl' in tab:
                url = tab.get('url', '')
                if 'cocrea' in url or 'sprunki' in url or 'scratch' in url:
                    return tab['webSocketDebuggerUrl']
        # Fallback: tab đầu tiên
        for tab in tabs:
            if tab.get('type') == 'page' and 'webSocketDebuggerUrl' in tab:
                return tab['webSocketDebuggerUrl']
        return None
    except Exception as e:
        print(f"[ERROR] Cannot connect to Chrome debug port {port}: {e}")
        return None


def send_cdp(ws, method, params=None):
    cmd = {"id": 1, "method": method, "params": params or {}}
    ws.send(json.dumps(cmd))
    # Đọc cho đến khi nhận được response có id=1
    for _ in range(50):
        raw = ws.recv()
        msg = json.loads(raw)
        if msg.get('id') == 1:
            return msg
    return None


def evaluate_js(ws, expression):
    return send_cdp(ws, "Runtime.evaluate", {
        "expression": expression,
        "awaitPromise": True,
        "returnByValue": True,
        "timeout": 15000
    })


# =============================================================================
# Hiển thị kết quả
# =============================================================================
def print_separator(title=""):
    print("\n" + "=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)


def print_target(t, label=""):
    prefix = f"  [{label}] " if label else "  "
    vis = "✅ VISIBLE" if t.get('visible') else "❌ hidden"
    print(f"{prefix}[{t['index']:3d}] {t['name']!r:30s} | costume: {t['currentCostume']!r:20s} | "
          f"pixel: ({t['x_pixel']:4d}, {t['y_pixel']:4d}) | {vis}")
    if t.get('costumes') and len(t['costumes']) > 1:
        print(f"         all costumes: {t['costumes']}")


def run_diagnostic():
    print_separator("SPRUNKI GAME PANEL DETECTOR")
    print("  Mục đích: Phát hiện tất cả sprite/button sau khi click Run / Re-Run")
    print("  Thời gian:", time.strftime("%Y-%m-%d %H:%M:%S"))

    ws_url = get_websocket_url(DEBUG_PORT)
    if not ws_url:
        print(f"\n[FATAL] Không tìm được Chrome debug tab ở port {DEBUG_PORT}")
        print("  Kiểm tra Chrome có đang mở với --remote-debugging-port=9222 không")
        return

    print(f"\n[OK] WebSocket URL: {ws_url}")

    try:
        ws = websocket.create_connection(
            ws_url,
            timeout=20
        )
        print("[OK] WebSocket connected")

        print("\n[...] Đang quét game targets...")
        response = evaluate_js(ws, JS_SCAN_ALL_TARGETS)
        ws.close()

    except Exception as e:
        print(f"\n[ERROR] WebSocket error: {e}")
        return

    # Parse kết quả
    if not response or 'result' not in response:
        print("[ERROR] Không nhận được response từ CDP")
        return

    result_wrapper = response.get('result', {}).get('result', {})
    if result_wrapper.get('type') == 'object' and 'value' in result_wrapper:
        data = result_wrapper['value']
    else:
        print(f"[ERROR] Unexpected result type: {result_wrapper}")
        return

    if not data.get('success'):
        print(f"\n[FAIL] VM không tìm thấy. Lý do: {data.get('reason')}")
        print("  → Game chưa load xong, hoặc trang không phải Sprunki/Scratch")
        return

    # -------------------------------------------------------------------------
    print_separator(f"TỔNG QUAN: {data['totalTargets']} targets trong VM")
    print(f"  Visible (non-stage): {len(data['visibleNonStage'])}")
    print(f"  Likely button candidates: {len(data['likelyCandidates'])}")
    print(f"  DOM buttons: {len(data['domButtons'])}")

    # -------------------------------------------------------------------------
    print_separator("⭐ LIKELY BUTTON CANDIDATES (visible + tên/costume khớp từ khoá)")
    if data['likelyCandidates']:
        for t in data['likelyCandidates']:
            print_target(t, "CANDIDATE")
    else:
        print("  (không tìm thấy candidate nào)")

    # -------------------------------------------------------------------------
    print_separator("👁 TẤT CẢ SPRITES ĐANG VISIBLE (non-stage)")
    if data['visibleNonStage']:
        for t in data['visibleNonStage']:
            print_target(t)
    else:
        print("  (không có sprite nào visible)")

    # -------------------------------------------------------------------------
    print_separator("🌐 DOM BUTTONS (HTML overlay buttons)")
    if data['domButtons']:
        for b in data['domButtons']:
            if 'error' in b:
                print(f"  [DOM scan error] {b['error']}")
            else:
                print(f"  [{b['index']:2d}] <{b['tag']}> text={b['text']!r:30s} | "
                      f"class={b['className'][:40]!r} | pixel: ({b['x_pixel']}, {b['y_pixel']})")
    else:
        print("  (không có DOM button nào)")

    # -------------------------------------------------------------------------
    print_separator("📋 DUMP ĐẦY ĐỦ (tất cả targets, kể cả hidden)")
    print("  Format: [idx] name | currentCostume | pixel(x,y) | visible")
    for t in data['allTargets']:
        if t['isStage']:
            continue
        vis_mark = "✅" if t['visible'] else "  "
        print(f"  {vis_mark} [{t['index']:3d}] {t['name']!r:30s} | {t['currentCostume']!r:20s} | "
              f"({t['x_pixel']:4d},{t['y_pixel']:4d})")

    # -------------------------------------------------------------------------
    # Ghi file JSON để phân tích thêm
    out_file = "panel_scan_result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print_separator(f"✅ XONG — Kết quả đầy đủ đã lưu vào: {out_file}")
    print("  Mở file đó để xem toàn bộ costumes, tọa độ logic, v.v.")


if __name__ == "__main__":
    run_diagnostic()

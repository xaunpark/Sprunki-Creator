# Remove Horror Detection from JSON Generator

> Created: 2026-05-04
> Status: Draft

## Summary
The current `json_generator.py` spends approximately 1 to 1.5 minutes executing "horror mode detection" by dragging Icon 19 (and a fallback higher-index icon) and waiting 25 seconds for each test. We want to completely remove this detection logic to vastly speed up the JSON generation process and maintain a clean game state, while preserving the existing pipeline architecture (e.g., closing Chrome after JSON generation and reopening it for the game player).

## Problem Statement
The Sprunki automation pipeline uses `json_generator.py` to identify mod features (such as slots, icons, game control type, and horror triggers). The horror detection phase is excessively slow (involves two 25-second `time.sleep` calls) and inherently pollutes the game's state because many mods do not allow returning to a "normal" state once horror mode is triggered. This forces the system to fully restart the Chrome instance after JSON generation.

By removing horror detection:
1. We save ~1m10s per execution.
2. We prevent the game from entering a dirty "horror" state.
3. The system defaults to producing only normal mode (`n-x.mp4`) and bonus mode (`b-x.mp4`) videos.

## Research Findings

### Codebase Patterns
- `json_generator.py` (`generate_complete_json`): Contains logic for dragging Icon 19, taking screenshots, comparing A vs B, and then trying the highest index icon.
- `sprunki_game_player.py`: Depends on `gameSettings.hasHorror` to perform horror-specific video sequences. If false, it simply skips them.
- `sprunki_ultimate_master.py`: Checks `item.json_data.get('gameSettings', {}).get('hasHorror', False)` to queue `h-x.mp4` recordings. If false, no horror videos are queued.

### Impact Analysis
- **What breaks?** Nothing technically breaks. The system is designed to gracefully handle `hasHorror = False`.
- **Side effects:** The system will simply not generate `h-*.mp4` (horror individual) videos for any mods automatically. If horror videos are desired, `hasHorror` must be manually flipped to `true` in the generated JSON by the operator.

## Proposed Solution

### Approach
1. In `json_generator.py::generate_complete_json`, remove the entire section dedicated to "DRAG ICON 19 & CAPTURE B" and "IF NO HORROR YET: Try Highest Index Icon".
2. Hardcode the final variables:
   ```python
   final_has_horror = False
   final_is_icon_19_disabled = False
   final_detection_method = "horror_detection_disabled"
   final_confidence = 1.0
   final_visual_result = {}
   ```
3. Remove the generation and comparison of State B and State C, and the `extended_area` screenshot captures used for SSIM/histogram comparison, since they are no longer needed.
4. Jump directly from "State A extraction" to "Final Layout Capture".
5. Keep the browser cleanup (`cleanup_chrome_complete()`) at the end of the generator unchanged, preserving the old architecture flow.

## Acceptance Criteria
- [ ] `json_generator.py` no longer drags Icon 19 or the highest-index icon.
- [ ] No 25-second sleep delays remain for horror detection.
- [ ] The generated JSON contains `"hasHorror": false` and `"isIcon19Disabled": false`.
- [ ] Chrome is still gracefully closed after JSON generation completes.
- [ ] The overall JSON generation process drops to under ~1m00s.

## Technical Considerations

### Risks
- Mods that exclusively rely on horror mode for their core functionality will be treated as normal mods. This is an acceptable tradeoff for the user.

### Alternatives Considered
- **Keep Horror Detection but shorten sleep:** Shrinking the 25-second wait to 5 seconds was considered. However, this doesn't solve the "dirty state" problem, which prevents the pipeline from potentially keeping Chrome open in the future.

## Implementation Steps

**Approach:**
1. Locate `generate_complete_json` in `json_generator.py`.
2. Delete lines ~2059 to ~2327 (from the creation of `extended_area` and capture of `screenshot_A`, through State B extraction, Phase/Screenshot comparison, and State C fallback).
3. Ensure `final_has_horror`, `final_is_icon_19_disabled`, `final_confidence`, `final_detection_method`, and `final_visual_result` are correctly initialized right before final layout capture.

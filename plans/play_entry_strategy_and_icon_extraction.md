# Play Entry Strategy and Icon Extraction Plan

## Goals

1. Let `json_generator` learn the correct way to enter/play each mod.
2. Save the verified entry strategy into generated JSON.
3. Let `sprunki_game_player` reuse the verified strategy instead of rediscovering it.
4. Keep icon extraction deterministic; do not add heuristic fallback based on lower-screen position/noise-name filtering.

## Playable Definition

A play/skip candidate is considered valid only after behavioral verification:

1. Click the candidate.
2. Confirm Scratch VM/runtime is still accessible.
3. Find at least one source icon and one playable slot.
4. Drag a test icon into a test slot.
5. Verify `characters` and/or `puttedCharacters` changes from `0` to `1`.
6. Return the test icon.
7. Verify counts return from `1` to `0`.

`target.visible`, sprite names, and costume names are only debug signals. They must not be treated as proof that the game is playable.

## JSON Schema Addition

Add `gameSettings.entryStrategy`:

```json
{
  "entryStrategy": {
    "selectedPlayButton": {
      "targetName": "Play Button3",
      "currentCostumeName": "costume1",
      "runtimeIndex": 78,
      "logicX": -180,
      "logicY": -24,
      "x": 481,
      "y": 609
    },
    "testedCandidates": [
      {
        "targetName": "Play Button5",
        "x": 539,
        "y": 402,
        "result": "not_playable"
      },
      {
        "targetName": "Play Button3",
        "x": 481,
        "y": 609,
        "result": "playable"
      }
    ],
    "verification": {
      "method": "drag_put_return",
      "dragSuccess": true,
      "returnSuccess": true,
      "charactersBefore": 0,
      "charactersAfterDrag": 1,
      "charactersAfterReturn": 0,
      "puttedBefore": 0,
      "puttedAfterDrag": 1,
      "puttedAfterReturn": 0
    }
  }
}
```

Store both `logicX`/`logicY` and pixel `x`/`y` so coordinates can be recalculated if viewport scaling changes.

## Required Code Changes

### 1. Unify Skip/Play Detection

`sprunki_game_player` currently detects general action buttons by keyword:

- `skip`
- `play`
- `start`
- `intro`

`json_generator` currently has a narrower skip detector that only recognizes target name `Intro` with costume `Skip`. This misses mods such as:

- `SkipCutscene/button`

Change `json_generator` to use the same general detector as `sprunki_game_player`, or extract a shared detector string/helper so both flows stay in sync.

This is the highest-priority fix for `SprunkePhase9aDefinitiveGabsTake`.

Observed in probes:

- `sprunki_game_player` detects and clicks `SkipCutscene/button`.
- `json_generator` does not detect it because its detector only searches for target name `Intro` with costume `Skip`.
- When the correct skip flow is used, the existing `Icons`-name icon rule finds 20 icons.
- Therefore `layout.icons = []` in the generated JSON is likely caused by capturing layout before the cutscene/menu was skipped correctly, not by this mod lacking `Icons` sprites.

### 2. Candidate Trial Instead of Click-All

When multiple candidates are found, do not click all of them.

Process:

1. Click one candidate.
2. Inspect the post-click runtime state and record diagnostics:
   - visible/action-like targets and current costumes,
   - detected icon/slot counts,
   - whether menu/panel-like targets appear active/open,
   - whether drag/put/return verification passes.
3. Treat drag/put success as necessary but not sufficient; it can still pass while a menu/gallery/settings/credits panel visually covers the game.
4. If pass, save it as `selectedPlayButton`.
5. If fail or if a panel/menu state is detected, rollback using Esc/close/reload and try the next candidate.

This addresses mods where multiple candidates are named like `Play Button2/3/4/5` but only one is the real Play button.

### 3. Reuse Entry Strategy in Player

`sprunki_game_player` should:

1. Read `gameSettings.entryStrategy.selectedPlayButton`.
2. Click that stored target/coordinate after Run/fullscreen.
3. Verify lightly that the game became playable.
4. Only fallback to discovery if the stored strategy fails.

### 4. Icon Extraction Policy

Current `json_generator` icon extraction only uses:

```js
allTargets.filter(t => t.sprite.name.includes('Icons') && t.visible)
```

For `SprunkePhase9aDefinitiveGabsTake`, a later probe showed that the existing `Icons` rule does find 20 icons after the correct `SkipCutscene/button` entry flow is used. In that specific mod, icon extraction failed because the layout was captured in the wrong game state.

Generated JSON previously had:

```json
"icons": []
```

Policy:

1. Keep the existing `Icons`-name rule.
2. Do not use fallback based on lower-screen position plus noisy-name exclusions; it can select wrong sprites in messy mods.
3. If `Icons` returns zero after verified entry/playable flow, fail JSON generation with a clear reason and log runtime target diagnostics.
4. Treat any future non-`Icons` icon support as a separate explicit detector, not a broad heuristic fallback.

## Current Findings

### UntitledProject44

Likely issue: multiple `Play Button*` targets plus center Gallery/menu controls. Old code clicked all candidates and then blindly clicked center `(960, 540)`, which can open Gallery.

Mitigation already added:

- Exclude obvious menu target names in `sprunki_game_player`.
- Skip blind center clicks when smart clicks already happened.
- Apply similar blind-click guard in `json_generator`.

Remaining work:

- Implement candidate trial with playable verification.
- Save/reuse `entryStrategy`.

### SprunkePhase9aDefinitiveGabsTake

Not the same Gallery issue.

Observed:

- Light Run/runtime inspect succeeds.
- Full entry finds `SkipCutscene/button` in `sprunki_game_player`.
- `json_generator` does not find this skip target because its detector is too narrow.
- Runtime inspect reports `visibleIconCount = 20`.
- Generated JSON has `layout.icons = []`.

Primary fixes:

- Unify skip/play detector.
- Keep the existing `Icons` extraction and verify that it runs after the correct skip/play entry state.

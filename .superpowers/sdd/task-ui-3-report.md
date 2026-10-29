# Task UI-3 Report: Episodes drawer + fullscreen integration

**Status:** Complete

## Changes made to `templates/index.html`

### Added (lines ~2155-2270)
- `buildEpisodesDrawer()` — creates collapsible episodes drawer with toggle button
- `toggleEpisodesDrawer()` — expands/collapses drawer
- `buildEpisodesGridInDrawer()` — populates drawer with episode buttons + download indicators
- Overlay sync event listeners: play, pause, volumechange, fullscreenchange, webkitfullscreenchange, enterpictureinpicture, leavepictureinpicture
- Desktop auto-fullscreen on first play (`document.documentElement.requestFullscreen()`)

### Replaced
- `playEpisodesListDirect()` — now builds overlay + drawer instead of old `gridEp` approach. Uses `playerOverlay.showToast()` for empty episodes.
- `play()` title rendering — removed `const epTitleEl` and both `epTitleEl.innerHTML` assignments. Replaced with `playerOverlay.setTitle()`, `resetInactivityTimer()`, `startSeekSync()`, `updatePlayPauseIcon()`. HLS/MP4 detection and video source logic preserved.
- `exitPlayer()` — simplified (removed verbose comments), added overlay cleanup (`stopSeekSync()`, DOM removal), episodes drawer removal.

## Concerns
- Desktop auto-fullscreen on first play may conflict with iOS native fullscreen on line 2215. Both use `hasEnteredFullscreen` flag — whichever fires first wins. Should be benign since iOS won't have `requestFullscreen` and desktop won't have `webkitSupportsFullscreen`.
- Add `hasEnteredFullscreen` reference works correctly — already declared at top of playerOverlay block.

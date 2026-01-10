# Image Clicker Android

Android counterpart to the desktop Image Clicker GUI. It captures the screen via MediaProjection, performs template matching, and triggers taps using an Accessibility Service.

## Features
- Capture a template from the current screen and crop it.
- Per-template matching threshold and click settings.
- Manual or match-center click modes.
- Optional jitter and delay.
- Stop all on match (per template).
- Foreground matching service with status updates.
- OpenCV-based matching with a fallback matcher.
- Performance optimizations (downscale + ROI search window).
- Auto-save settings, bulk enable/disable/remove, and a match log panel.

## Requirements
- Android Studio Hedgehog or newer
- Android 8.0+ (minSdk 26)

## Build
```bash
cd image_clicker_android
./gradlew assembleDebug
```

## Run (Step by Step)
1) Build APK:
```bash
cd image_clicker_android
./gradlew assembleDebug
```

2) Install APK:
```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

3) Enable Accessibility Service:
- Open Android Settings -> Accessibility -> Installed services
- Turn on "Image Clicker"

4) Capture a template:
- Open the target app/screen
- Return to Image Clicker and tap "Capture Template"
- Select a region and confirm

5) Adjust template settings:
- Set threshold, click mode, button, jitter, delays, etc.
- Tap "Save Template Settings" (or just wait for auto-save)

6) Start matching:
- Tap "Start Matching"
- Grant the screen capture permission

7) Stop matching:
- Tap "Stop Matching" in the app or stop the foreground service notification

## Notes / Differences from Desktop
- Android does not support a real mouse cursor. "Right" clicks map to a long-press.
- "After Click: restore" is treated as no-op; only manual after-click is supported.
- Matching uses OpenCV when available; a SAD-based fallback remains for safety.

## File Layout
- `app/src/main/java/com/picomouse/imageclicker/` core logic and UI.
- `app/src/main/res/layout/` layouts for main and crop screens.
- `app/src/main/AndroidManifest.xml` permissions, services.

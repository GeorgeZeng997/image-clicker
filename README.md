# Image Clicker GUI

Image Clicker GUI is a small desktop tool that lets you capture on-screen templates
and automatically click when those templates are detected. It runs entirely on your
local machine and scans the full screen at a fixed interval.

## Features
- Capture a template directly from the screen.
- Match templates with a configurable threshold.
- Click modes: match center or fixed manual coordinates.
- Click customization: button, single/double, delay, jitter.
- Optional "stop all on match" per template.
- Auto right-click toggle.
- Live preview of the selected template.

## Requirements
- Windows 10/11
- Python 3.9+

Python packages:
- opencv-python
- mss
- pillow
- pynput

## Install
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
python image_clicker.py
```

## How It Works
1. Capture a template from the screen.
2. The app converts the template to grayscale and stores it on disk.
3. Every 2 seconds, the full screen is captured and compared using `matchTemplate`.
4. When a match exceeds the threshold, a click action is performed.

## Hotkeys
- F1: Toggle auto right-click.
- F2: Copy current cursor position into the manual X/Y fields.
- F3: Toggle matching.

## Configuration
Settings are stored in `config.json`. Each template includes:
- `threshold`: Match score (0.0 - 1.0).
- `click_mode`: `match` or `manual`.
- `manual_x`, `manual_y`: Used when `click_mode` is `manual`.
- `button`: `left`, `right`, `middle`.
- `click_type`: `single` or `double`.
- `jitter_enabled`, `jitter_radius`: Random offset in pixels.
- `click_delay_min_ms`, `click_delay_max_ms`, `click_delay_random`.
- `enabled`: Template on/off.
- `stop_all_on_match`: Stop matching after this template triggers.
- `after_click_mode`: `restore` or `manual`.
- `after_click_x`, `after_click_y`: Used when `after_click_mode` is `manual`.

## Notes
- The app masks its own window during matching to reduce self-matches.
- Higher thresholds reduce false positives but may miss noisy templates.

## License
MIT. See `LICENSE`.

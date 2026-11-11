# Image Clicker (Desktop GUI + Headless CLI)

**Image Clicker** is a High-DPI-aware OpenCV template-matching automation tool with both an interactive **Tkinter Desktop GUI** and a scriptable **Headless CLI** (featuring batch template-directory queues, `--dry-run` coordinate preview, JSON output, and a top-left `(0, 0)` emergency cursor fail-safe).

---

## Key Features

- **Per-Monitor High-DPI Awareness**: Automatically enables Windows Per-Monitor DPI Awareness (`SetProcessDpiAwareness`) so physical screen captures (`mss`) and cursor coordinates (`pynput`) stay pixel-accurate at 125% / 150% / 200% scaling.
- **Interactive Screen Capture & File Import**: Drag-select regions directly from your screen or batch-import existing `.png/.jpg/.bmp/.webp` template assets into an ordered matching queue.
- **Dry-Run Preview Mode (GUI & CLI)**: Inspect matched template names, `TM_CCOEFF_NORMED` confidence scores, and `(x, y)` click targets without moving or clicking the mouse.
- **Emergency Top-Left `(0, 0)` Fail-Safe**: Slamming the mouse cursor into the top-left corner (`0..4 px`) immediately halts active clicking loops.
- **Rich Click Customization**: Left / right / middle button, single / double click, randomized pixel jitter radius, configurable pre-click delay (`min..max ms`), and post-click cursor restoration.
- **Scriptable CLI & Offline Screenshot Testing**: Scan live displays or saved screenshots (`--screenshot`) with `--templates-dir`, `--threshold`, `--interval`, `--max-clicks`, and `--json`.

---

## Installation

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

---

## Usage

### 1. Launch Desktop GUI (Default)

```bash
python image_clicker.py
```

#### Global Hotkeys (GUI Mode)
- `F1`: Toggle periodic auto right-click.
- `F2`: Copy current cursor `(X, Y)` into manual target fields.
- `F3`: Start / Stop template matching.
- **Top-Left `(0, 0)` Corner**: Emergency fail-safe stop.

### 2. Headless CLI & Dry-Run Mode

```bash
# Dry-run a directory queue of templates against the live screen (preview coordinates only)
python image_clicker.py --templates-dir ./buttons --threshold 0.85 --interval 1.0 --dry-run --max-scans 5

# Offline CI / calibration check against a saved screenshot with structured JSON output
python image_clicker.py --screenshot ./screen.png --template ./ok_btn.png --dry-run --json

# Automated live clicking (stops after first match)
python image_clicker.py --template ./confirm.png --threshold 0.9 --stop-on-match
```

---

## Running Tests

```bash
python -m unittest discover -s tests -v
```

---

## License

MIT. See `LICENSE`.

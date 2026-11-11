import argparse
import ctypes
import json
import os
import platform
import random
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

SCAN_INTERVAL_SEC = 2.0
DEFAULT_THRESHOLD = 0.8
AUTO_CLICK_MIN_SEC = 3.0
AUTO_CLICK_MAX_SEC = 5.0
FAILSAFE_CORNER_MARGIN_PX = 4
SUPPORTED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def enable_windows_dpi_awareness() -> bool:
    """Enable Per-Monitor DPI Awareness on Windows so screen capture and mouse coords align."""
    if platform.system() != "Windows":
        return False
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:
            return False


def clamp_threshold(value: Any, default: float = DEFAULT_THRESHOLD) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not (0.0 <= num <= 1.0):
        return max(0.0, min(1.0, num)) if num == num else default
    return num


def safe_int(value: Any, default: int = 0, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = default
    if min_val is not None:
        num = max(min_val, num)
    if max_val is not None:
        num = min(max_val, num)
    return num


def safe_storage_path(target_path: str, base_dir: str) -> str:
    """Return a relative path when on the same drive/mount, or an absolute path across drives."""
    abs_target = os.path.abspath(target_path)
    abs_base = os.path.abspath(base_dir)
    try:
        return os.path.relpath(abs_target, abs_base)
    except ValueError:
        return abs_target


def resolve_storage_path(stored_path: str, base_dir: str) -> str:
    if not stored_path:
        return ""
    if os.path.isabs(stored_path):
        return stored_path
    return os.path.normpath(os.path.join(base_dir, stored_path))


def is_failsafe_triggered(position: Tuple[int, int], margin: int = FAILSAFE_CORNER_MARGIN_PX) -> bool:
    """Return True if cursor is inside the top-left emergency abort corner (0..margin, 0..margin)."""
    x, y = position
    return 0 <= x <= margin and 0 <= y <= margin


@dataclass
class MatchResult:
    template_name: str
    score: float
    top_left: Tuple[int, int]
    target_xy: Tuple[int, int]


@dataclass
class TemplateItem:
    name: str
    image_path: str
    image_gray: np.ndarray
    width: int
    height: int
    threshold: float = DEFAULT_THRESHOLD
    click_mode: str = "match"  # "match" or "manual"
    manual_x: int = 0
    manual_y: int = 0
    button: str = "left"  # "left" | "right" | "middle"
    click_type: str = "single"  # "single" | "double"
    jitter_enabled: bool = False
    jitter_radius: int = 0
    click_delay_min_ms: int = 0
    click_delay_max_ms: int = 0
    click_delay_random: bool = False
    enabled: bool = True
    stop_all_on_match: bool = False
    after_click_mode: str = "restore"
    after_click_x: int = 0
    after_click_y: int = 0

    def match_position(self, max_loc: Tuple[int, int], monitor_left: int, monitor_top: int) -> Tuple[int, int]:
        if self.click_mode == "manual":
            return self.manual_x, self.manual_y
        x = monitor_left + max_loc[0] + self.width // 2
        y = monitor_top + max_loc[1] + self.height // 2
        return x, y


def load_template_item(
    image_path: str,
    name: Optional[str] = None,
    threshold: float = DEFAULT_THRESHOLD,
    button: str = "left",
    click_type: str = "single",
    jitter_radius: int = 0,
    stop_all_on_match: bool = False,
) -> Optional[TemplateItem]:
    abs_path = os.path.abspath(image_path)
    if not os.path.isfile(abs_path):
        return None
    image_bgr = cv2.imread(abs_path)
    if image_bgr is None or image_bgr.size == 0:
        return None
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    tpl_name = name or os.path.splitext(os.path.basename(abs_path))[0]
    return TemplateItem(
        name=tpl_name,
        image_path=abs_path,
        image_gray=gray,
        width=int(gray.shape[1]),
        height=int(gray.shape[0]),
        threshold=clamp_threshold(threshold),
        click_mode="match",
        manual_x=0,
        manual_y=0,
        button=button if button in ("left", "right", "middle") else "left",
        click_type=click_type if click_type in ("single", "double") else "single",
        jitter_enabled=jitter_radius > 0,
        jitter_radius=max(0, int(jitter_radius)),
        enabled=True,
        stop_all_on_match=bool(stop_all_on_match),
    )


def discover_templates_in_dir(
    directory: str,
    threshold: float = DEFAULT_THRESHOLD,
    button: str = "left",
    click_type: str = "single",
    jitter_radius: int = 0,
    stop_all_on_match: bool = False,
) -> List[TemplateItem]:
    if not os.path.isdir(directory):
        return []
    items: List[TemplateItem] = []
    for entry in sorted(os.listdir(directory)):
        if entry.lower().endswith(SUPPORTED_IMAGE_EXTS):
            full = os.path.join(directory, entry)
            item = load_template_item(
                full,
                threshold=threshold,
                button=button,
                click_type=click_type,
                jitter_radius=jitter_radius,
                stop_all_on_match=stop_all_on_match,
            )
            if item is not None:
                items.append(item)
    return items


def load_templates_from_config(config_path: str) -> List[TemplateItem]:
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(data, list):
        templates_data = data
        after_click_default: Dict[str, Any] = {}
    elif isinstance(data, dict):
        templates_data = data.get("templates", [])
        after_click_default = data.get("after_click", {})
    else:
        return []

    base_dir = os.path.dirname(os.path.abspath(config_path))
    items: List[TemplateItem] = []
    for entry in templates_data:
        if not isinstance(entry, dict):
            continue
        image_path = resolve_storage_path(str(entry.get("image_path", "")), base_dir)
        if not image_path or not os.path.isfile(image_path):
            continue
        image_bgr = cv2.imread(image_path)
        if image_bgr is None or image_bgr.size == 0:
            continue
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        button = str(entry.get("button", "left"))
        if button not in ("left", "right", "middle"):
            button = "left"
        click_type = str(entry.get("click_type", "single"))
        if click_type not in ("single", "double"):
            click_type = "single"
        click_mode = str(entry.get("click_mode", "match"))
        if click_mode not in ("match", "manual"):
            click_mode = "match"
        after_mode = str(entry.get("after_click_mode", after_click_default.get("mode", "restore")))
        if after_mode not in ("restore", "manual", "none"):
            after_mode = "restore"

        items.append(
            TemplateItem(
                name=str(entry.get("name", "Template")),
                image_path=image_path,
                image_gray=gray,
                width=int(gray.shape[1]),
                height=int(gray.shape[0]),
                threshold=clamp_threshold(entry.get("threshold", DEFAULT_THRESHOLD)),
                click_mode=click_mode,
                manual_x=safe_int(entry.get("manual_x", 0)),
                manual_y=safe_int(entry.get("manual_y", 0)),
                button=button,
                click_type=click_type,
                jitter_enabled=bool(entry.get("jitter_enabled", False)),
                jitter_radius=safe_int(entry.get("jitter_radius", 0), 0, 0, 500),
                click_delay_min_ms=safe_int(entry.get("click_delay_min_ms", 0), 0, 0, 60000),
                click_delay_max_ms=safe_int(entry.get("click_delay_max_ms", 0), 0, 0, 60000),
                click_delay_random=bool(entry.get("click_delay_random", False)),
                enabled=bool(entry.get("enabled", True)),
                stop_all_on_match=bool(entry.get("stop_all_on_match", False)),
                after_click_mode=after_mode,
                after_click_x=safe_int(entry.get("after_click_x", after_click_default.get("x", 0))),
                after_click_y=safe_int(entry.get("after_click_y", after_click_default.get("y", 0))),
            )
        )
    return items


def find_template_matches(
    screen_gray: np.ndarray,
    templates: Sequence[TemplateItem],
    monitor_left: int = 0,
    monitor_top: int = 0,
) -> List[Tuple[TemplateItem, MatchResult]]:
    """Scan grayscale frame against enabled templates in queue order."""
    matches: List[Tuple[TemplateItem, MatchResult]] = []
    sh, sw = screen_gray.shape[:2]
    for item in templates:
        if not item.enabled:
            continue
        if item.height > sh or item.width > sw or item.height <= 0 or item.width <= 0:
            continue
        try:
            res = cv2.matchTemplate(screen_gray, item.image_gray, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
        except cv2.error:
            continue
        if float(max_val) >= item.threshold:
            target_xy = item.match_position(max_loc, monitor_left, monitor_top)
            matches.append(
                (
                    item,
                    MatchResult(
                        template_name=item.name,
                        score=round(float(max_val), 4),
                        top_left=(int(max_loc[0]), int(max_loc[1])),
                        target_xy=target_xy,
                    ),
                )
            )
            if item.stop_all_on_match:
                break
    return matches


def grab_screen_bgr() -> Tuple[Optional[np.ndarray], int, int]:
    try:
        import mss  # lazy import so CLI --screenshot / unit tests work without mss installed

        with mss.mss() as sct:
            monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            img = np.array(shot)[:, :, :3]
            return img, int(monitor["left"]), int(monitor["top"])
    except Exception:
        return None, 0, 0


class CaptureWindow:
    def __init__(self, root: Any, screenshot: np.ndarray, monitor_left: int, monitor_top: int) -> None:
        import tkinter as tk
        from PIL import Image, ImageTk

        self._tk = tk
        self.root = root
        self.screenshot = screenshot
        self.monitor_left = monitor_left
        self.monitor_top = monitor_top
        self.selection: Optional[Tuple[int, int, int, int]] = None

        height, width = screenshot.shape[:2]
        self.top = tk.Toplevel(self.root)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.configure(cursor="cross")

        self.canvas = tk.Canvas(self.top, width=width, height=height, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        image_rgb = cv2.cvtColor(self.screenshot, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(image_rgb)
        self.photo = ImageTk.PhotoImage(pil)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

        self.start_x = 0
        self.start_y = 0
        self.rect_id = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", self.on_cancel)

    def on_press(self, event: Any) -> None:
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(self.start_x, self.start_y, event.x, event.y, outline="red")

    def on_drag(self, event: Any) -> None:
        if self.rect_id is not None:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event: Any) -> None:
        x1, y1 = self.start_x, self.start_y
        x2, y2 = event.x, event.y
        left = min(x1, x2)
        right = max(x1, x2)
        top = min(y1, y2)
        bottom = max(y1, y2)
        if right - left < 5 or bottom - top < 5:
            self.selection = None
        else:
            self.selection = (left, top, right, bottom)
        self.top.destroy()

    def on_cancel(self, event: Any) -> None:
        self.selection = None
        self.top.destroy()


class ImageClickerApp:
    def __init__(self, root: Any) -> None:
        import tkinter as tk
        from tkinter import ttk
        from pynput import keyboard, mouse

        self._tk = tk
        self._ttk = ttk
        self._keyboard = keyboard
        self._mouse = mouse

        self.root = root
        self.root.title("Image Clicker")
        self.root.geometry("860x720")
        self.root.minsize(800, 650)

        self.templates: List[TemplateItem] = []
        self.selected_index: Optional[int] = None

        self.enabled = False
        self.auto_right_enabled = False
        self.stop_flag = threading.Event()
        self.mouse_controller = mouse.Controller()

        self.status_var = tk.StringVar(value="Idle")
        self.match_count_var = tk.StringVar(value="0")
        self.auto_right_var = tk.StringVar(value="Auto Right: OFF")
        self.coord_var = tk.StringVar(value="X=0 Y=0")
        self.last_match_var = tk.StringVar(value="Last match: None")
        self.last_match_time_var = tk.StringVar(value="Last time: --")
        self.last_match_score_var = tk.StringVar(value="Last score: --")
        self.scan_interval_var = tk.StringVar(value=str(SCAN_INTERVAL_SEC))
        self.dry_run_var = tk.BooleanVar(value=False)
        self.failsafe_enabled_var = tk.BooleanVar(value=True)

        self.after_click_mode_var = tk.StringVar(value="restore")
        self.after_click_x_var = tk.StringVar(value="0")
        self.after_click_y_var = tk.StringVar(value="0")
        self.stop_all_on_match_var = tk.BooleanVar(value=False)

        self.threshold_var = tk.StringVar(value=str(DEFAULT_THRESHOLD))
        self.click_mode_var = tk.StringVar(value="match")
        self.manual_x_var = tk.StringVar(value="0")
        self.manual_y_var = tk.StringVar(value="0")
        self.button_var = tk.StringVar(value="left")
        self.click_type_var = tk.StringVar(value="single")
        self.jitter_enabled_var = tk.BooleanVar(value=False)
        self.jitter_radius_var = tk.StringVar(value="0")
        self.click_delay_min_var = tk.StringVar(value="0")
        self.click_delay_max_var = tk.StringVar(value="0")
        self.click_delay_random_var = tk.BooleanVar(value=False)
        self.template_enabled_var = tk.BooleanVar(value=True)
        self.preview_canvas: Optional[Any] = None
        self.preview_photo: Optional[Any] = None
        self.preview_image_id: Optional[int] = None
        self._auto_save_after_id: Optional[str] = None
        self._suppress_auto_save = False

        self._build_ui()
        self._setup_hotkey()
        self._start_coord_poll()
        self._bind_auto_save()
        self._load_config()

        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()
        self.auto_right_thread = threading.Thread(target=self.auto_right_loop, daemon=True)
        self.auto_right_thread.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        tk = self._tk
        ttk = self._ttk
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")

        ttk.Label(left, text="Templates Queue").pack(anchor="w")
        self.listbox = tk.Listbox(left, height=12)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        self.listbox.bind("<<ListboxSelect>>", self.on_select_template)

        ttk.Button(left, text="Capture Screen Region", command=self.add_template).pack(fill=tk.X)
        ttk.Button(left, text="Import Image File(s)", command=self.import_template_files).pack(fill=tk.X, pady=(4, 0))
        ttk.Button(left, text="Remove Selected", command=self.remove_template).pack(fill=tk.X, pady=(4, 0))

        engine_frame = ttk.LabelFrame(left, text="Engine Controls", padding=6)
        engine_frame.pack(fill=tk.X, pady=(10, 0))
        interval_row = ttk.Frame(engine_frame)
        interval_row.pack(fill=tk.X)
        ttk.Label(interval_row, text="Scan Interval (s):").pack(side=tk.LEFT)
        ttk.Entry(interval_row, textvariable=self.scan_interval_var, width=6).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Checkbutton(engine_frame, text="Dry-Run (Preview Only, No Clicks)", variable=self.dry_run_var).pack(
            anchor="w", pady=(4, 0)
        )
        ttk.Checkbutton(engine_frame, text="Top-Left (0,0) Emergency Fail-Safe", variable=self.failsafe_enabled_var).pack(
            anchor="w"
        )

        status_frame = ttk.Frame(left)
        status_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(status_frame, text="Status:").grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.status_var).grid(row=0, column=1, sticky="w")
        ttk.Label(status_frame, text="Matches:").grid(row=1, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.match_count_var).grid(row=1, column=1, sticky="w")
        ttk.Label(status_frame, text="Cursor:").grid(row=2, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.coord_var).grid(row=2, column=1, sticky="w")
        ttk.Label(status_frame, textvariable=self.auto_right_var).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(status_frame, textvariable=self.last_match_var).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(status_frame, textvariable=self.last_match_time_var).grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Label(status_frame, textvariable=self.last_match_score_var).grid(row=6, column=0, columnspan=2, sticky="w")

        self.toggle_btn = ttk.Button(left, text="Start", command=self.toggle)
        self.toggle_btn.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(right, text="Template Settings").grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(right, text="Threshold (0.0-1.0):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(right, textvariable=self.threshold_var, width=10).grid(row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(right, text="Click Mode:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        mode_frame = ttk.Frame(right)
        mode_frame.grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Radiobutton(mode_frame, text="Match Center", variable=self.click_mode_var, value="match").pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Manual Coord", variable=self.click_mode_var, value="manual").pack(anchor="w")

        ttk.Label(right, text="Manual X/Y:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        manual_frame = ttk.Frame(right)
        manual_frame.grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Entry(manual_frame, textvariable=self.manual_x_var, width=10).pack(side=tk.LEFT)
        ttk.Entry(manual_frame, textvariable=self.manual_y_var, width=10).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(right, text="Button:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            right, textvariable=self.button_var, values=["left", "right", "middle"], width=10, state="readonly"
        ).grid(row=4, column=1, sticky="w", pady=(6, 0))

        ttk.Label(right, text="Click Type:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            right, textvariable=self.click_type_var, values=["single", "double"], width=10, state="readonly"
        ).grid(row=5, column=1, sticky="w", pady=(6, 0))

        ttk.Checkbutton(right, text="Template Enabled", variable=self.template_enabled_var).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Checkbutton(right, text="Stop all on match", variable=self.stop_all_on_match_var).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        ttk.Label(right, text="Random Offset:").grid(row=8, column=0, sticky="w", pady=(6, 0))
        jitter_frame = ttk.Frame(right)
        jitter_frame.grid(row=8, column=1, sticky="w", pady=(6, 0))
        ttk.Checkbutton(jitter_frame, text="Enable", variable=self.jitter_enabled_var).pack(side=tk.LEFT)
        ttk.Entry(jitter_frame, textvariable=self.jitter_radius_var, width=8).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(jitter_frame, text="px").pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(right, text="After Click Move:").grid(row=9, column=0, sticky="w", pady=(6, 0))
        after_frame = ttk.Frame(right)
        after_frame.grid(row=9, column=1, sticky="w", pady=(6, 0))
        ttk.Radiobutton(after_frame, text="Restore", variable=self.after_click_mode_var, value="restore").pack(anchor="w")
        ttk.Radiobutton(after_frame, text="Manual", variable=self.after_click_mode_var, value="manual").pack(anchor="w")
        ttk.Radiobutton(after_frame, text="Stay", variable=self.after_click_mode_var, value="none").pack(anchor="w")
        after_xy = ttk.Frame(right)
        after_xy.grid(row=10, column=1, sticky="w", pady=(2, 0))
        ttk.Entry(after_xy, textvariable=self.after_click_x_var, width=8).pack(side=tk.LEFT)
        ttk.Entry(after_xy, textvariable=self.after_click_y_var, width=8).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(right, text="Click Delay (ms):").grid(row=11, column=0, sticky="w", pady=(6, 0))
        delay_frame = ttk.Frame(right)
        delay_frame.grid(row=11, column=1, sticky="w", pady=(6, 0))
        ttk.Entry(delay_frame, textvariable=self.click_delay_min_var, width=6).pack(side=tk.LEFT)
        ttk.Label(delay_frame, text="to").pack(side=tk.LEFT, padx=(4, 4))
        ttk.Entry(delay_frame, textvariable=self.click_delay_max_var, width=6).pack(side=tk.LEFT)
        ttk.Checkbutton(delay_frame, text="Random", variable=self.click_delay_random_var).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(right, text="Preview (full size):").grid(row=12, column=0, columnspan=2, sticky="w", pady=(12, 0))
        preview_frame = ttk.Frame(right)
        preview_frame.grid(row=13, column=0, columnspan=2, sticky="nsew")
        preview_canvas = tk.Canvas(preview_frame, width=360, height=240, relief="groove", highlightthickness=1)
        x_scroll = ttk.Scrollbar(preview_frame, orient="horizontal", command=preview_canvas.xview)
        y_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=preview_canvas.yview)
        preview_canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        preview_canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_canvas = preview_canvas

        ttk.Label(
            right,
            text="Hotkeys: F1 auto right-click | F2 capture cursor X/Y | F3 start/stop | Move mouse to (0,0) to abort.",
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=(10, 0))

        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(13, weight=1)

        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

    def _current_scan_interval(self) -> float:
        try:
            val = float(self.scan_interval_var.get().strip())
            return max(0.05, min(3600.0, val))
        except ValueError:
            return SCAN_INTERVAL_SEC

    def toggle(self) -> None:
        if self.enabled:
            self.enabled = False
            self.toggle_btn.configure(text="Start")
            self.status_var.set("Idle")
        else:
            if not self.templates:
                self.status_var.set("Add a template first.")
                return
            self.enabled = True
            self.toggle_btn.configure(text="Stop")
            self.status_var.set("Running (Dry-Run)" if self.dry_run_var.get() else "Running")

    def _start_coord_poll(self) -> None:
        def update() -> None:
            if self.stop_flag.is_set():
                return
            x, y = self.mouse_controller.position
            self.coord_var.set(f"X={x} Y={y}")
            self.root.after(100, update)

        update()

    def _setup_hotkey(self) -> None:
        keyboard = self._keyboard

        def on_press(key: Any) -> None:
            if key == keyboard.Key.f1:
                self.root.after(0, self.toggle_auto_right)
            elif key == keyboard.Key.f2:
                x, y = self.mouse_controller.position
                self.root.after(0, self._set_manual_coords, x, y)
            elif key == keyboard.Key.f3:
                self.root.after(0, self.toggle_matching_hotkey)

        self.key_listener = keyboard.Listener(on_press=on_press)
        self.key_listener.start()

    def _set_manual_coords(self, x: int, y: int) -> None:
        self.manual_x_var.set(str(x))
        self.manual_y_var.set(str(y))
        self.status_var.set(f"Manual coords set to X={x} Y={y}")

    def _update_preview(self, item: TemplateItem) -> None:
        if self.preview_canvas is None:
            return
        from PIL import Image, ImageTk

        image_bgr = cv2.imread(item.image_path)
        if image_bgr is None:
            self._clear_preview()
            return
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(image_rgb)
        self.preview_photo = ImageTk.PhotoImage(pil)
        self.preview_canvas.delete("all")
        self.preview_image_id = self.preview_canvas.create_image(0, 0, image=self.preview_photo, anchor="nw")
        self.preview_canvas.config(scrollregion=(0, 0, pil.width, pil.height))

    def _clear_preview(self) -> None:
        if self.preview_canvas is None:
            return
        self.preview_photo = None
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(10, 10, anchor="nw", text="No template selected")
        self.preview_canvas.config(scrollregion=(0, 0, 0, 0))

    def toggle_matching_hotkey(self) -> None:
        if self.enabled:
            self.enabled = False
            self.toggle_btn.configure(text="Start")
            self.status_var.set("Matching stopped.")
        else:
            if not self.templates:
                self.status_var.set("Add a template first.")
                return
            self.enabled = True
            self.toggle_btn.configure(text="Stop")
            self.status_var.set("Matching started.")

    def _stop_matching_from_worker(self, reason: str = "Matching stopped (matched).") -> None:
        self.enabled = False
        self.auto_right_enabled = False
        self.auto_right_var.set("Auto Right: OFF")
        self.toggle_btn.configure(text="Start")
        self.status_var.set(reason)

    def import_template_files(self) -> None:
        from tkinter import filedialog

        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Import Template Image(s)",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All Files", "*.*")],
        )
        if not paths:
            return
        added = 0
        for p in paths:
            item = load_template_item(p)
            if item is not None:
                self.templates.append(item)
                self.listbox.insert(self._tk.END, item.name)
                added += 1
        if added > 0:
            self.listbox.selection_clear(0, self._tk.END)
            self.listbox.selection_set(self._tk.END)
            self.on_select_template(None)
            self.status_var.set(f"Imported {added} template(s).")
            self._save_config()

    def add_template(self) -> None:
        from tkinter import simpledialog

        screenshot, left, top = self.capture_screen()
        if screenshot is None:
            self.status_var.set("Capture failed.")
            return

        capture = CaptureWindow(self.root, screenshot, left, top)
        self.root.wait_window(capture.top)
        if capture.selection is None:
            self.status_var.set("Selection canceled.")
            return

        x1, y1, x2, y2 = capture.selection
        cropped = screenshot[y1:y2, x1:x2]
        if cropped.size == 0:
            self.status_var.set("Invalid selection.")
            return

        name = simpledialog.askstring("Template Name", "Enter a name for this template:", parent=self.root)
        if not name:
            name = f"Template {len(self.templates) + 1}"

        image_path = self._save_template_image(name, cropped)
        if image_path is None:
            self.status_var.set("Failed to save template image.")
            return

        gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
        current_click_mode = self.click_mode_var.get()
        item = TemplateItem(
            name=name,
            image_path=image_path,
            image_gray=gray,
            width=gray.shape[1],
            height=gray.shape[0],
            threshold=DEFAULT_THRESHOLD,
            click_mode=current_click_mode,
            manual_x=left + x1,
            manual_y=top + y1,
            button="left",
            click_type="single",
            jitter_enabled=False,
            jitter_radius=0,
            click_delay_min_ms=0,
            click_delay_max_ms=0,
            click_delay_random=False,
            enabled=True,
            stop_all_on_match=False,
            after_click_mode=self.after_click_mode_var.get(),
            after_click_x=safe_int(self.after_click_x_var.get().strip(), 0),
            after_click_y=safe_int(self.after_click_y_var.get().strip(), 0),
        )
        self.templates.append(item)
        self.listbox.insert(self._tk.END, item.name)
        self.listbox.selection_clear(0, self._tk.END)
        self.listbox.selection_set(self._tk.END)
        self.on_select_template(None)
        self.status_var.set("Template added.")
        self._save_config()

    def remove_template(self) -> None:
        index = self.selected_index
        if index is None:
            return
        item = self.templates[index]
        # Only delete auto-captured images inside TEMPLATE_DIR, never user-imported external assets
        if os.path.abspath(os.path.dirname(item.image_path)) == os.path.abspath(TEMPLATE_DIR):
            self._delete_template_image(item.image_path)
        self.templates.pop(index)
        self.listbox.delete(index)
        self.selected_index = None
        self._clear_preview()
        self.status_var.set("Template removed.")
        self._save_config()

    def on_select_template(self, event: Optional[Any]) -> None:
        if not self.listbox.curselection():
            return
        index = self.listbox.curselection()[0]
        self.selected_index = index
        item = self.templates[index]
        self._suppress_auto_save = True
        self.threshold_var.set(str(item.threshold))
        self.click_mode_var.set(item.click_mode)
        self.manual_x_var.set(str(item.manual_x))
        self.manual_y_var.set(str(item.manual_y))
        self.button_var.set(item.button)
        self.click_type_var.set(item.click_type)
        self.template_enabled_var.set(item.enabled)
        self.stop_all_on_match_var.set(item.stop_all_on_match)
        self.jitter_enabled_var.set(item.jitter_enabled)
        self.jitter_radius_var.set(str(item.jitter_radius))
        self.click_delay_min_var.set(str(item.click_delay_min_ms))
        self.click_delay_max_var.set(str(item.click_delay_max_ms))
        self.click_delay_random_var.set(bool(item.click_delay_random))
        self.after_click_mode_var.set(item.after_click_mode)
        self.after_click_x_var.set(str(item.after_click_x))
        self.after_click_y_var.set(str(item.after_click_y))
        self._suppress_auto_save = False
        self._update_preview(item)

    def _bind_auto_save(self) -> None:
        def hook(*_args: object) -> None:
            if self._suppress_auto_save:
                return
            if self._auto_save_after_id is not None:
                self.root.after_cancel(self._auto_save_after_id)
            self._auto_save_after_id = self.root.after(300, self._auto_save_settings)

        for var in (
            self.threshold_var,
            self.click_mode_var,
            self.manual_x_var,
            self.manual_y_var,
            self.button_var,
            self.click_type_var,
            self.template_enabled_var,
            self.stop_all_on_match_var,
            self.jitter_enabled_var,
            self.jitter_radius_var,
            self.click_delay_min_var,
            self.click_delay_max_var,
            self.click_delay_random_var,
            self.after_click_mode_var,
            self.after_click_x_var,
            self.after_click_y_var,
        ):
            var.trace_add("write", hook)

    def _auto_save_settings(self) -> None:
        self._auto_save_after_id = None
        index = self.selected_index
        if index is None or index >= len(self.templates):
            return
        item = self.templates[index]
        try:
            threshold = float(self.threshold_var.get().strip())
            manual_x = int(self.manual_x_var.get().strip())
            manual_y = int(self.manual_y_var.get().strip())
            jitter_radius = int(self.jitter_radius_var.get().strip())
            after_x = int(self.after_click_x_var.get().strip())
            after_y = int(self.after_click_y_var.get().strip())
            click_delay_min = int(self.click_delay_min_var.get().strip())
            click_delay_max = int(self.click_delay_max_var.get().strip())
        except ValueError:
            return
        if threshold < 0.0 or threshold > 1.0:
            return
        if jitter_radius < 0 or jitter_radius > 500:
            return
        if click_delay_min < 0 or click_delay_max < 0:
            return
        if click_delay_min > 60000 or click_delay_max > 60000:
            return
        if self.after_click_mode_var.get() not in ("restore", "manual", "none"):
            return

        item.threshold = threshold
        item.click_mode = self.click_mode_var.get()
        item.manual_x = manual_x
        item.manual_y = manual_y
        item.button = self.button_var.get()
        item.click_type = self.click_type_var.get()
        item.enabled = bool(self.template_enabled_var.get())
        item.stop_all_on_match = bool(self.stop_all_on_match_var.get())
        item.jitter_enabled = bool(self.jitter_enabled_var.get())
        item.jitter_radius = jitter_radius
        item.click_delay_min_ms = click_delay_min
        item.click_delay_max_ms = click_delay_max
        item.click_delay_random = bool(self.click_delay_random_var.get())
        item.after_click_mode = self.after_click_mode_var.get()
        item.after_click_x = after_x
        item.after_click_y = after_y
        self._save_config()

    def capture_screen(self) -> Tuple[Optional[np.ndarray], int, int]:
        return grab_screen_bgr()

    def worker_loop(self) -> None:
        while not self.stop_flag.is_set():
            if not self.enabled:
                self.stop_flag.wait(0.1)
                continue

            if self.failsafe_enabled_var.get() and is_failsafe_triggered(self.mouse_controller.position):
                self.root.after(
                    0,
                    self._stop_matching_from_worker,
                    "Emergency fail-safe triggered (cursor at top-left).",
                )
                continue

            screenshot, left, top = self.capture_screen()
            if screenshot is None:
                self.stop_flag.wait(self._current_scan_interval())
                continue

            gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
            self._mask_app_window(gray, left, top)

            hits = find_template_matches(gray, list(self.templates), left, top)
            match_count = len(hits)

            for item, mres in hits:
                match_time = time.strftime("%H:%M:%S", time.localtime())
                x, y = mres.target_xy
                self.root.after(0, self.last_match_var.set, f"Last match: {item.name} ({x},{y})")
                self.root.after(0, self.last_match_time_var.set, f"Last time: {match_time}")
                self.root.after(0, self.last_match_score_var.set, f"Last score: {mres.score:.3f}")
                if not self.dry_run_var.get():
                    aborted = self.perform_click(
                        x,
                        y,
                        item.button,
                        item.click_type,
                        item.jitter_enabled,
                        item.jitter_radius,
                        item.click_delay_min_ms,
                        item.click_delay_max_ms,
                        item.click_delay_random,
                        item.after_click_mode,
                        item.after_click_x,
                        item.after_click_y,
                    )
                    if aborted:
                        break
                if item.stop_all_on_match:
                    self.root.after(0, self._stop_matching_from_worker)
                    break

            self.root.after(0, self.match_count_var.set, str(match_count))
            self.stop_flag.wait(self._current_scan_interval())

    def _mask_app_window(self, gray: np.ndarray, monitor_left: int, monitor_top: int) -> None:
        try:
            if not self.root.winfo_viewable():
                return
            win_x = self.root.winfo_rootx()
            win_y = self.root.winfo_rooty()
            win_w = self.root.winfo_width()
            win_h = self.root.winfo_height()
        except Exception:
            return
        if win_w <= 0 or win_h <= 0:
            return
        x1 = max(0, win_x - monitor_left)
        y1 = max(0, win_y - monitor_top)
        x2 = min(gray.shape[1], win_x - monitor_left + win_w)
        y2 = min(gray.shape[0], win_y - monitor_top + win_h)
        if x2 <= x1 or y2 <= y1:
            return
        gray[y1:y2, x1:x2] = 0

    def auto_right_loop(self) -> None:
        while not self.stop_flag.is_set():
            if not self.auto_right_enabled:
                self.stop_flag.wait(0.1)
                continue
            interval = random.uniform(AUTO_CLICK_MIN_SEC, AUTO_CLICK_MAX_SEC)
            if self.stop_flag.wait(interval):
                break
            if not self.auto_right_enabled:
                continue
            if not self.dry_run_var.get():
                self.perform_click(
                    *self.mouse_controller.position,
                    "right",
                    "single",
                    False,
                    0,
                    0,
                    0,
                    False,
                    "none",
                    0,
                    0,
                )

    def perform_click(
        self,
        x: int,
        y: int,
        button: str,
        click_type: str,
        jitter_enabled: bool,
        jitter_radius: int,
        click_delay_min_ms: int,
        click_delay_max_ms: int,
        click_delay_random: bool,
        after_click_mode: str,
        after_click_x: int,
        after_click_y: int,
    ) -> bool:
        """Execute click and return True if aborted by emergency fail-safe."""
        original_pos = self.mouse_controller.position
        if self.failsafe_enabled_var.get() and is_failsafe_triggered(original_pos):
            self.root.after(
                0,
                self._stop_matching_from_worker,
                "Emergency fail-safe triggered (cursor at top-left).",
            )
            return True

        if jitter_enabled and jitter_radius > 0:
            x += random.randint(-jitter_radius, jitter_radius)
            y += random.randint(-jitter_radius, jitter_radius)
        if click_delay_max_ms < click_delay_min_ms:
            click_delay_min_ms, click_delay_max_ms = click_delay_max_ms, click_delay_min_ms
        if click_delay_max_ms > 0:
            if click_delay_random:
                delay = random.uniform(click_delay_min_ms, click_delay_max_ms) / 1000.0
            else:
                delay = click_delay_min_ms / 1000.0
            if delay > 0 and self.stop_flag.wait(delay):
                return True

        self.mouse_controller.position = (x, y)
        btn = {
            "left": self._mouse.Button.left,
            "right": self._mouse.Button.right,
            "middle": self._mouse.Button.middle,
        }.get(button, self._mouse.Button.left)
        if click_type == "double":
            self.mouse_controller.click(btn, 2)
        else:
            self.mouse_controller.click(btn, 1)
        self._move_after_click(original_pos, after_click_mode, after_click_x, after_click_y)
        return False

    def _move_after_click(self, original_pos: Tuple[int, int], mode: str, x: int, y: int) -> None:
        if mode == "none":
            return
        if mode == "restore":
            self.mouse_controller.position = original_pos
            return
        if mode == "manual":
            self.mouse_controller.position = (x, y)

    def toggle_auto_right(self) -> None:
        self.auto_right_enabled = not self.auto_right_enabled
        if self.auto_right_enabled:
            self.auto_right_var.set("Auto Right: ON")
        else:
            self.auto_right_var.set("Auto Right: OFF")

    def on_close(self) -> None:
        self.stop_flag.set()
        self.enabled = False
        self.auto_right_enabled = False
        try:
            self.key_listener.stop()
        except Exception:
            pass
        self._save_config()
        self.root.destroy()

    def _ensure_template_dir(self) -> None:
        os.makedirs(TEMPLATE_DIR, exist_ok=True)

    def _make_safe_filename(self, name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
        if not cleaned:
            cleaned = "template"
        base = cleaned
        counter = 1
        while os.path.exists(os.path.join(TEMPLATE_DIR, f"{base}.png")):
            counter += 1
            base = f"{cleaned}_{counter}"
        return f"{base}.png"

    def _save_template_image(self, name: str, image_bgr: np.ndarray) -> Optional[str]:
        self._ensure_template_dir()
        filename = self._make_safe_filename(name)
        path = os.path.join(TEMPLATE_DIR, filename)
        if cv2.imwrite(path, image_bgr):
            return path
        return None

    def _delete_template_image(self, path: str) -> None:
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def _save_config(self) -> None:
        self._ensure_template_dir()
        base_dir = os.path.dirname(os.path.abspath(CONFIG_PATH))
        data = {"templates": []}
        for item in self.templates:
            data["templates"].append(
                {
                    "name": item.name,
                    "image_path": safe_storage_path(item.image_path, base_dir),
                    "threshold": item.threshold,
                    "click_mode": item.click_mode,
                    "manual_x": item.manual_x,
                    "manual_y": item.manual_y,
                    "button": item.button,
                    "click_type": item.click_type,
                    "jitter_enabled": item.jitter_enabled,
                    "jitter_radius": item.jitter_radius,
                    "click_delay_min_ms": item.click_delay_min_ms,
                    "click_delay_max_ms": item.click_delay_max_ms,
                    "click_delay_random": item.click_delay_random,
                    "enabled": item.enabled,
                    "stop_all_on_match": item.stop_all_on_match,
                    "after_click_mode": item.after_click_mode,
                    "after_click_x": item.after_click_x,
                    "after_click_y": item.after_click_y,
                }
            )
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def _load_config(self) -> None:
        loaded = load_templates_from_config(CONFIG_PATH)
        self.templates.clear()
        self.listbox.delete(0, self._tk.END)
        for item in loaded:
            self.templates.append(item)
            self.listbox.insert(self._tk.END, item.name)


def run_cli(args: argparse.Namespace) -> int:
    templates: List[TemplateItem] = []
    if args.config and os.path.isfile(args.config):
        templates.extend(load_templates_from_config(args.config))

    for tpl_path in args.template or []:
        item = load_template_item(
            tpl_path,
            threshold=args.threshold,
            button=args.button,
            click_type=args.click_type,
            jitter_radius=args.jitter,
            stop_all_on_match=args.stop_on_match,
        )
        if item is not None:
            templates.append(item)

    if args.templates_dir:
        templates.extend(
            discover_templates_in_dir(
                args.templates_dir,
                threshold=args.threshold,
                button=args.button,
                click_type=args.click_type,
                jitter_radius=args.jitter,
                stop_all_on_match=args.stop_on_match,
            )
        )

    if not templates and os.path.isfile(CONFIG_PATH):
        templates.extend(load_templates_from_config(CONFIG_PATH))

    if not templates:
        print("Error: No valid templates loaded. Provide --template, --templates-dir, or --config.", file=sys.stderr)
        return 1

    mouse_ctrl = None
    mouse_mod = None
    if not args.dry_run:
        from pynput import mouse as _mouse_mod

        mouse_mod = _mouse_mod
        mouse_ctrl = _mouse_mod.Controller()

    total_clicks = 0
    scan_idx = 0
    events: List[Dict[str, Any]] = []

    try:
        while True:
            scan_idx += 1
            if args.screenshot:
                frame = cv2.imread(args.screenshot)
                if frame is None:
                    print(f"Error: Failed to load screenshot '{args.screenshot}'.", file=sys.stderr)
                    return 1
                left, top = 0, 0
            else:
                frame, left, top = grab_screen_bgr()
                if frame is None:
                    print("Error: Screen capture failed (ensure 'mss' is installed and a display is active).", file=sys.stderr)
                    return 1

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hits = find_template_matches(gray, templates, left, top)

            for item, mres in hits:
                x, y = mres.target_xy
                if item.jitter_enabled and item.jitter_radius > 0:
                    x += random.randint(-item.jitter_radius, item.jitter_radius)
                    y += random.randint(-item.jitter_radius, item.jitter_radius)

                event = {
                    "scan": scan_idx,
                    "template": mres.template_name,
                    "score": mres.score,
                    "x": x,
                    "y": y,
                    "button": item.button,
                    "click_type": item.click_type,
                    "dry_run": bool(args.dry_run),
                }
                events.append(event)

                if not args.json:
                    mode_tag = "[DRY-RUN]" if args.dry_run else "[CLICK]"
                    print(
                        f"{mode_tag} template='{mres.template_name}' score={mres.score:.4f} target=({x}, {y}) button={item.button}/{item.click_type}"
                    )

                if not args.dry_run and mouse_ctrl is not None and mouse_mod is not None:
                    orig_pos = mouse_ctrl.position
                    if not args.no_failsafe and is_failsafe_triggered(orig_pos):
                        print("Aborted: Emergency fail-safe triggered at top-left corner (0, 0).", file=sys.stderr)
                        return 2
                    mouse_ctrl.position = (x, y)
                    btn = {
                        "left": mouse_mod.Button.left,
                        "right": mouse_mod.Button.right,
                        "middle": mouse_mod.Button.middle,
                    }.get(item.button, mouse_mod.Button.left)
                    mouse_ctrl.click(btn, 2 if item.click_type == "double" else 1)
                    if item.after_click_mode == "restore":
                        mouse_ctrl.position = orig_pos

                total_clicks += 1
                if args.max_clicks and total_clicks >= args.max_clicks:
                    break

            if args.screenshot or (args.max_scans and scan_idx >= args.max_scans):
                break
            if args.max_clicks and total_clicks >= args.max_clicks:
                break
            if args.stop_on_match and hits:
                break

            time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        pass

    if args.json:
        print(
            json.dumps(
                {
                    "scans_completed": scan_idx,
                    "templates_loaded": len(templates),
                    "matches_triggered": total_clicks,
                    "dry_run": bool(args.dry_run),
                    "events": events,
                },
                indent=2,
            )
        )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="image-clicker",
        description="High-DPI OpenCV Template Matcher & Auto-Clicker (Tkinter Desktop GUI + Headless CLI)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless CLI scanning mode instead of launching the Tkinter GUI.",
    )
    parser.add_argument(
        "-t",
        "--template",
        action="append",
        help="Path to a template image (repeatable for multi-target queue).",
    )
    parser.add_argument(
        "-d",
        "--templates-dir",
        help="Directory of template images (.png/.jpg/.bmp/.webp) to enqueue in sorted order.",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="Optional config.json file defining templates and per-template click behaviors.",
    )
    parser.add_argument(
        "--screenshot",
        help="Run a single offline scan against a saved screenshot image instead of live screen capture.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Match confidence threshold 0.0..1.0 (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=SCAN_INTERVAL_SEC,
        help=f"Seconds between screen scans (default: {SCAN_INTERVAL_SEC}).",
    )
    parser.add_argument(
        "--button",
        choices=["left", "right", "middle"],
        default="left",
        help="Mouse button for CLI templates (default: left).",
    )
    parser.add_argument(
        "--click-type",
        choices=["single", "double"],
        default="single",
        help="Single or double click for CLI templates (default: single).",
    )
    parser.add_argument(
        "--jitter",
        type=int,
        default=0,
        help="Random +/- pixel offset radius for CLI templates (default: 0).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview matched coordinates and confidence scores without moving or clicking the mouse.",
    )
    parser.add_argument(
        "--max-clicks",
        type=int,
        default=0,
        help="Exit automatically after N total matches/clicks (0 = unlimited).",
    )
    parser.add_argument(
        "--max-scans",
        type=int,
        default=0,
        help="Exit automatically after N scan iterations (0 = unlimited).",
    )
    parser.add_argument(
        "--stop-on-match",
        action="store_true",
        help="Stop immediately after the first matched template.",
    )
    parser.add_argument(
        "--no-failsafe",
        action="store_true",
        help="Disable top-left (0, 0) emergency cursor fail-safe check.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON summary in CLI mode.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    enable_windows_dpi_awareness()
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if raw_args:
        parser = build_arg_parser()
        args = parser.parse_args(raw_args)
        if (
            args.headless
            or args.template
            or args.templates_dir
            or args.screenshot
            or args.dry_run
            or args.json
            or args.config
        ):
            return run_cli(args)

    import tkinter as tk

    root = tk.Tk()
    ImageClickerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

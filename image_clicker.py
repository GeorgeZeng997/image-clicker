import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import mss
import numpy as np
import tkinter as tk
from PIL import Image, ImageTk
from pynput import keyboard, mouse
from tkinter import simpledialog, ttk


SCAN_INTERVAL_SEC = 2.0
DEFAULT_THRESHOLD = 0.8
AUTO_CLICK_MIN_SEC = 3.0
AUTO_CLICK_MAX_SEC = 5.0
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


@dataclass
class TemplateItem:
    name: str
    image_path: str
    image_gray: np.ndarray
    width: int
    height: int
    threshold: float
    click_mode: str  # "match" or "manual"
    manual_x: int
    manual_y: int
    button: str  # "left" | "right" | "middle"
    click_type: str  # "single" | "double"
    jitter_enabled: bool
    jitter_radius: int
    click_delay_min_ms: int
    click_delay_max_ms: int
    click_delay_random: bool
    enabled: bool
    stop_all_on_match: bool
    after_click_mode: str
    after_click_x: int
    after_click_y: int

    def match_position(self, max_loc: Tuple[int, int], monitor_left: int, monitor_top: int) -> Tuple[int, int]:
        if self.click_mode == "manual":
            return self.manual_x, self.manual_y
        x = monitor_left + max_loc[0] + self.width // 2
        y = monitor_top + max_loc[1] + self.height // 2
        return x, y


class CaptureWindow:
    def __init__(self, root: tk.Tk, screenshot: np.ndarray, monitor_left: int, monitor_top: int) -> None:
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

    def on_press(self, event: tk.Event) -> None:
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(self.start_x, self.start_y, event.x, event.y, outline="red")

    def on_drag(self, event: tk.Event) -> None:
        if self.rect_id is not None:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event: tk.Event) -> None:
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

    def on_cancel(self, event: tk.Event) -> None:
        self.selection = None
        self.top.destroy()


class ImageClickerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Image Clicker")
        self.root.geometry("820x680")
        self.root.minsize(780, 620)

        self.templates: List[TemplateItem] = []
        self.selected_index: Optional[int] = None

        self.enabled = False
        self.auto_right_enabled = False
        self.stop_flag = threading.Event()
        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()
        self.auto_right_thread = threading.Thread(target=self.auto_right_loop, daemon=True)
        self.auto_right_thread.start()

        self.mouse_controller = mouse.Controller()

        self.status_var = tk.StringVar(value="Idle")
        self.match_count_var = tk.StringVar(value="0")
        self.auto_right_var = tk.StringVar(value="Auto Right: OFF")
        self.coord_var = tk.StringVar(value="X=0 Y=0")
        self.last_match_var = tk.StringVar(value="Last match: None")
        self.last_match_time_var = tk.StringVar(value="Last time: --")
        self.last_match_score_var = tk.StringVar(value="Last score: --")
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
        self.preview_canvas: Optional[tk.Canvas] = None
        self.preview_photo: Optional[ImageTk.PhotoImage] = None
        self.preview_image_id: Optional[int] = None
        self._auto_save_after_id: Optional[str] = None
        self._suppress_auto_save = False

        self._build_ui()
        self._setup_hotkey()
        self._start_coord_poll()
        self._bind_auto_save()
        self._load_config()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")

        ttk.Label(left, text="Templates").pack(anchor="w")
        self.listbox = tk.Listbox(left, height=14)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        self.listbox.bind("<<ListboxSelect>>", self.on_select_template)

        ttk.Button(left, text="Add Template", command=self.add_template).pack(fill=tk.X)
        ttk.Button(left, text="Remove Template", command=self.remove_template).pack(fill=tk.X, pady=(6, 0))

        status_frame = ttk.Frame(left)
        status_frame.pack(fill=tk.X, pady=(12, 0))
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
        ttk.Label(status_frame, text="Stop all on match: per template").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        self.toggle_btn = ttk.Button(left, text="Start", command=self.toggle)
        self.toggle_btn.pack(fill=tk.X, pady=(12, 0))

        ttk.Label(right, text="Template Settings").grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(right, text="Threshold:").grid(row=1, column=0, sticky="w", pady=(6, 0))
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
        ttk.Combobox(right, textvariable=self.button_var, values=["left", "right", "middle"], width=10, state="readonly").grid(
            row=4, column=1, sticky="w", pady=(6, 0)
        )

        ttk.Label(right, text="Click Type:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(right, textvariable=self.click_type_var, values=["single", "double"], width=10, state="readonly").grid(
            row=5, column=1, sticky="w", pady=(6, 0)
        )

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
        preview_canvas = tk.Canvas(preview_frame, width=360, height=260, relief="groove", highlightthickness=1)
        x_scroll = ttk.Scrollbar(preview_frame, orient="horizontal", command=preview_canvas.xview)
        y_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=preview_canvas.yview)
        preview_canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        preview_canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_canvas = preview_canvas

        ttk.Label(right, text="Changes auto-save. F1 auto right-click, F2 copy cursor, F3 toggle matching.").grid(
            row=14, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(13, weight=1)

        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

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
            self.status_var.set("Running")

    def _start_coord_poll(self) -> None:
        def update() -> None:
            x, y = self.mouse_controller.position
            self.coord_var.set(f"X={x} Y={y}")
            self.root.after(100, update)

        update()

    def _setup_hotkey(self) -> None:
        def on_press(key: keyboard.Key) -> None:
            if key == keyboard.Key.f1:
                self.root.after(0, self.toggle_auto_right)
            if key == keyboard.Key.f2:
                x, y = self.mouse_controller.position
                self.root.after(0, self._set_manual_coords, x, y)
            if key == keyboard.Key.f3:
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

    def _stop_matching_from_worker(self) -> None:
        if self.enabled:
            self.enabled = False
            self.toggle_btn.configure(text="Start")
            self.status_var.set("Matching stopped (matched).")

    def add_template(self) -> None:
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
            after_click_x=int(self.after_click_x_var.get().strip() or 0),
            after_click_y=int(self.after_click_y_var.get().strip() or 0),
        )
        self.templates.append(item)
        self.listbox.insert(tk.END, item.name)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(tk.END)
        self.on_select_template(None)
        self.status_var.set("Template added.")
        self._save_config()

    def remove_template(self) -> None:
        index = self.selected_index
        if index is None:
            return
        item = self.templates[index]
        self._delete_template_image(item.image_path)
        self.templates.pop(index)
        self.listbox.delete(index)
        self.selected_index = None
        self._clear_preview()
        self.status_var.set("Template removed.")
        self._save_config()

    def on_select_template(self, event: Optional[tk.Event]) -> None:
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
        if index is None:
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
        if self.after_click_mode_var.get() not in ("restore", "manual"):
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
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
                img = np.array(shot)[:, :, :3]
                return img, monitor["left"], monitor["top"]
        except Exception:
            return None, 0, 0

    def worker_loop(self) -> None:
        while not self.stop_flag.is_set():
            if not self.enabled:
                time.sleep(0.1)
                continue

            screenshot, left, top = self.capture_screen()
            if screenshot is None:
                time.sleep(SCAN_INTERVAL_SEC)
                continue

            gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
            self._mask_app_window(gray, left, top)
            match_count = 0

            for item in list(self.templates):
                if not item.enabled:
                    continue
                try:
                    res = cv2.matchTemplate(gray, item.image_gray, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    if max_val >= item.threshold:
                        match_count += 1
                        match_time = time.strftime("%H:%M:%S", time.localtime())
                        self.root.after(0, self.last_match_var.set, f"Last match: {item.name}")
                        self.root.after(0, self.last_match_time_var.set, f"Last time: {match_time}")
                        self.root.after(0, self.last_match_score_var.set, f"Last score: {max_val:.3f}")
                        x, y = item.match_position(max_loc, left, top)
                        self.perform_click(
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
                        if item.stop_all_on_match:
                            self.root.after(0, self._stop_matching_from_worker)
                            break
                except cv2.error:
                    continue

            if self.stop_all_on_match_var.get() and not self.enabled:
                time.sleep(SCAN_INTERVAL_SEC)
                continue

            self.root.after(0, self.match_count_var.set, str(match_count))
            time.sleep(SCAN_INTERVAL_SEC)

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
                time.sleep(0.1)
                continue
            interval = random.uniform(AUTO_CLICK_MIN_SEC, AUTO_CLICK_MAX_SEC)
            if self.stop_flag.wait(interval):
                break
            if not self.auto_right_enabled:
                continue
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
    ) -> None:
        original_pos = self.mouse_controller.position
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
            if delay > 0:
                time.sleep(delay)
        self.mouse_controller.position = (x, y)
        btn = {
            "left": mouse.Button.left,
            "right": mouse.Button.right,
            "middle": mouse.Button.middle,
        }.get(button, mouse.Button.left)
        if click_type == "double":
            self.mouse_controller.click(btn, 2)
        else:
            self.mouse_controller.click(btn, 1)
        self._move_after_click(original_pos, after_click_mode, after_click_x, after_click_y)

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
        data = {"templates": []}
        for item in self.templates:
            data["templates"].append(
                {
                    "name": item.name,
                    "image_path": os.path.relpath(item.image_path, os.path.dirname(__file__)),
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
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        if isinstance(data, list):
            templates_data = data
            after_click_default = {}
        else:
            templates_data = data.get("templates", [])
            after_click_default = data.get("after_click", {})

        self.templates.clear()
        self.listbox.delete(0, tk.END)

        for entry in templates_data:
            rel_path = entry.get("image_path", "")
            image_path = os.path.join(os.path.dirname(__file__), rel_path)
            if not os.path.exists(image_path):
                continue
            image_bgr = cv2.imread(image_path)
            if image_bgr is None:
                continue
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            item = TemplateItem(
                name=entry.get("name", "Template"),
                image_path=image_path,
                image_gray=gray,
                width=gray.shape[1],
                height=gray.shape[0],
                threshold=float(entry.get("threshold", DEFAULT_THRESHOLD)),
                click_mode=entry.get("click_mode", "match"),
                manual_x=int(entry.get("manual_x", 0)),
                manual_y=int(entry.get("manual_y", 0)),
                button=entry.get("button", "left"),
                click_type=entry.get("click_type", "single"),
                jitter_enabled=bool(entry.get("jitter_enabled", False)),
                jitter_radius=int(entry.get("jitter_radius", 0)),
                click_delay_min_ms=int(entry.get("click_delay_min_ms", 0)),
                click_delay_max_ms=int(entry.get("click_delay_max_ms", 0)),
                click_delay_random=bool(entry.get("click_delay_random", False)),
                enabled=bool(entry.get("enabled", True)),
                stop_all_on_match=bool(entry.get("stop_all_on_match", False)),
                after_click_mode=entry.get("after_click_mode", after_click_default.get("mode", "restore")),
                after_click_x=int(entry.get("after_click_x", after_click_default.get("x", 0))),
                after_click_y=int(entry.get("after_click_y", after_click_default.get("y", 0))),
            )
            self.templates.append(item)
            self.listbox.insert(tk.END, item.name)


def main() -> None:
    root = tk.Tk()
    app = ImageClickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

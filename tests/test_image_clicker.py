import json
import os
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

import cv2
import numpy as np

import image_clicker


class TestImageClickerEngine(unittest.TestCase):
    def test_bounds_and_failsafe_helpers(self) -> None:
        self.assertEqual(image_clicker.clamp_threshold(0.85), 0.85)
        self.assertEqual(image_clicker.clamp_threshold(1.5), 1.0)
        self.assertEqual(image_clicker.clamp_threshold(-0.3), 0.0)
        self.assertEqual(image_clicker.clamp_threshold("invalid", 0.75), 0.75)

        self.assertTrue(image_clicker.is_failsafe_triggered((0, 0)))
        self.assertTrue(image_clicker.is_failsafe_triggered((3, 4)))
        self.assertFalse(image_clicker.is_failsafe_triggered((10, 10)))

        # Cross-drive safe_storage_path fallback
        res = image_clicker.safe_storage_path(r"Z:\other_drive\tpl.png", r"C:\repo")
        if os.name == "nt":
            self.assertEqual(res, r"Z:\other_drive\tpl.png")

    def test_template_matching_queue_and_stop_on_match(self) -> None:
        # Build synthetic 240x320 dark screen with two distinct bright target patches
        screen = np.zeros((240, 320, 3), dtype=np.uint8)
        # Patch A centered at (60 + 20, 40 + 15) = (80, 55)
        cv2.rectangle(screen, (60, 40), (100, 70), (255, 255, 255), -1)
        cv2.circle(screen, (80, 55), 8, (40, 40, 40), -1)

        # Patch B centered at (200 + 16, 150 + 16) = (216, 166)
        cv2.rectangle(screen, (200, 150), (232, 182), (200, 200, 200), 2)
        cv2.line(screen, (200, 150), (232, 182), (255, 255, 255), 2)

        with tempfile.TemporaryDirectory() as tmp:
            tpl_dir = os.path.join(tmp, "templates")
            os.makedirs(tpl_dir, exist_ok=True)
            tpl_a_path = os.path.join(tpl_dir, "01_patch_a.png")
            tpl_b_path = os.path.join(tpl_dir, "02_patch_b.png")
            screen_path = os.path.join(tmp, "screen.png")

            cv2.imwrite(tpl_a_path, screen[40:70, 60:100])
            cv2.imwrite(tpl_b_path, screen[150:182, 200:232])
            cv2.imwrite(screen_path, screen)

            templates = image_clicker.discover_templates_in_dir(tpl_dir, threshold=0.9)
            self.assertEqual(len(templates), 2)

            gray_screen = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            hits = image_clicker.find_template_matches(gray_screen, templates, monitor_left=100, monitor_top=50)
            self.assertEqual(len(hits), 2)
            self.assertEqual(hits[0][1].template_name, "01_patch_a")
            self.assertEqual(hits[0][1].target_xy, (180, 105))
            self.assertGreaterEqual(hits[0][1].score, 0.99)
            self.assertEqual(hits[1][1].template_name, "02_patch_b")
            self.assertEqual(hits[1][1].target_xy, (316, 216))

            # Verify stop_all_on_match halts queue after first hit
            templates[0].stop_all_on_match = True
            stopped_hits = image_clicker.find_template_matches(gray_screen, templates)
            self.assertEqual(len(stopped_hits), 1)

            # Verify oversized template (> screen dimensions) skips gracefully without OpenCV assertion error
            oversized = image_clicker.TemplateItem(
                name="oversized",
                image_path=tpl_a_path,
                image_gray=np.zeros((500, 500), dtype=np.uint8),
                width=500,
                height=500,
            )
            self.assertEqual(image_clicker.find_template_matches(gray_screen, [oversized]), [])

            # Verify CLI --dry-run --json against saved screenshot
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = image_clicker.main(
                    [
                        "--screenshot",
                        screen_path,
                        "--templates-dir",
                        tpl_dir,
                        "--threshold",
                        "0.9",
                        "--dry-run",
                        "--json",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["templates_loaded"], 2)
            self.assertEqual(payload["matches_triggered"], 2)
            self.assertTrue(payload["dry_run"])

    def test_config_loader_handles_corrupt_and_valid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            img_path = os.path.join(tmp, "btn.png")
            img = np.full((20, 30, 3), 128, dtype=np.uint8)
            cv2.imwrite(img_path, img)

            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "templates": [
                            {
                                "name": "ConfirmBtn",
                                "image_path": "btn.png",
                                "threshold": 1.8,  # out-of-range -> clamped to 1.0
                                "button": "invalid_btn",  # -> defaults to left
                                "jitter_radius": "9999",  # -> clamped to 500
                            }
                        ]
                    },
                    f,
                )

            loaded = image_clicker.load_templates_from_config(cfg_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].name, "ConfirmBtn")
            self.assertEqual(loaded[0].threshold, 1.0)
            self.assertEqual(loaded[0].button, "left")
            self.assertEqual(loaded[0].jitter_radius, 500)


if __name__ == "__main__":
    unittest.main()

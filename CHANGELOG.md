# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-11-11
- Added Windows Per-Monitor High-DPI awareness so screen capture and click coordinates align on scaled displays.
- Added top-left `(0, 0)` emergency cursor fail-safe check and non-blocking thread shutdown waits.
- Added full CLI runner (`--headless`, `--template`, `--templates-dir`, `--screenshot`, `--dry-run`, `--max-clicks`, `--max-scans`, `--json`).
- Added GUI template file import, configurable scan interval, and Dry-Run preview toggle.
- Hardened cross-drive path serialization and config bounds validation; added unit tests and GitHub Actions CI.

## [0.1.0]
- Initial public release.

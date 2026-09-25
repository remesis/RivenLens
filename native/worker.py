# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Native screen worker, communicating through Qt signals rather than a server."""

import asyncio
import copy
import sys
import threading
import time
import traceback

import mss
from PIL import ImageOps
from PySide6.QtCore import QThread, Signal

from capture import CapturePending, CaptureUnavailable, DesktopCapture, monitor_token
from ocr_engine import LocalOCR
from ocr_budget import FrameBudget
from reader import read_frame, reset_layout
from tracker import RollTracker
from preferences import capture_settings
from catalog import Catalog


def enumerate_monitors():
    keys = ("left", "top", "width", "height", "unique_id", "name", "is_primary")
    with mss.MSS() as probe:
        return [{k: m[k] for k in keys if k in m} for m in probe.monitors[1:]]


class CaptureWorker(QThread):
    updated = Signal(object)
    displays = Signal(object, object)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._guard = threading.Lock()
        self._config = {**capture_settings(settings), "running": False}
        self._revision = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._monitors = []
        self._reported_errors = set()

    def configure(self, settings, running):
        with self._guard:
            self._config = {**capture_settings(settings), "running": bool(running)}
            if self._monitors:
                self._resolve_monitor(self._monitors)
            self._revision += 1
            revision = self._revision
        self._wake.set()
        return revision

    def _resolve_monitor(self, monitors):
        """Called under the configuration lock; UI indices never override identity."""
        token = self._config["monitorId"]
        index = self._config["monitor"] - 1
        if not token and 0 <= index < len(self._monitors):
            token = monitor_token(self._monitors[index])
        if token:
            matches = [i for i, m in enumerate(monitors) if monitor_token(m) == token]
        else:
            matches = [index] if 0 <= index < len(monitors) else []
        if len(matches) != 1:
            self._config.update(monitor=1, monitorId="", running=False)
        else:
            index = matches[0]
            self._config.update(
                monitor=index + 1, monitorId=monitor_token(monitors[index])
            )

    def shutdown(self):
        self._stop.set()
        self._wake.set()

    def _snapshot(self):
        with self._guard:
            return copy.deepcopy(self._config), self._revision

    def _publish(self, revision, **state):
        with self._guard:
            if revision != self._revision or self._stop.is_set():
                return
        self.updated.emit({"revision": revision, **copy.deepcopy(state)})

    def _record_error(self, stage, exc):
        """Keep bounded local diagnostics, never captured images or OCR readings."""
        identity = (stage, type(exc).__name__, str(exc)[:500])
        if identity in self._reported_errors or len(self._reported_errors) >= 20:
            return
        self._reported_errors.add(identity)
        if sys.stderr is not None:
            details = "".join(traceback.format_exception(exc, limit=8))
            print(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} {stage} failure\n{details[:8000]}",
                file=sys.stderr,
            )

    def run(self):
        try:
            asyncio.run(self._loop())
        except Exception as exc:
            self._record_error("worker", exc)
            _, revision = self._snapshot()
            self._publish(
                revision, status="error", cards=[], newRollWarning=None, error=str(exc)
            )

    async def _loop(self):
        engine = camera = None
        monitors, prior_revision = [], -1
        tracker = RollTracker()
        catalog = Catalog()
        check_at = retry_at = 0
        last_cards = []
        try:
            with mss.MSS() as screen:
                while not self._stop.is_set():
                    config, revision = self._snapshot()
                    if time.monotonic() >= check_at:
                        check_at = time.monotonic() + 2
                        try:
                            refreshed = enumerate_monitors()
                        except Exception as exc:
                            self._record_error("capture", exc)
                            self._publish(
                                revision,
                                status="error",
                                error=str(exc),
                                errorKind="capture",
                                newRollWarning=None,
                                cards=[
                                    {**card, "snapshot": True, "displayStale": True}
                                    for card in last_cards
                                ],
                            )
                            self._wake.wait(0.2)
                            self._wake.clear()
                            continue
                        if refreshed != monitors:
                            with self._guard:
                                self._resolve_monitor(refreshed)
                                if self._monitors:
                                    self._revision += 1
                                self._monitors = copy.deepcopy(refreshed)
                            monitors = refreshed
                            config, revision = self._snapshot()
                    if revision != prior_revision:
                        self.displays.emit(monitors, {**config, "revision": revision})
                        if camera:
                            camera.close()
                            camera = None
                        if engine:
                            reset_layout(engine)
                        tracker, last_cards = RollTracker(), []
                        prior_revision, retry_at = revision, 0
                        self._publish(
                            revision,
                            status="waiting" if config["running"] else "paused",
                            cards=[],
                            newRollWarning=None,
                            error=None,
                        )
                    if not config["running"] or time.monotonic() < retry_at:
                        self._wake.wait(0.1)
                        self._wake.clear()
                        continue
                    started = time.monotonic()
                    stage = "ocr"
                    try:
                        if engine is None:
                            engine = LocalOCR()
                        stage = "capture"
                        if camera is None:
                            camera = DesktopCapture(
                                screen,
                                monitors[config["monitor"] - 1],
                                config["backend"],
                            )
                        image = camera.grab(config["region"])
                        stage = "ocr"
                        rank_image = image
                        if config["contrast"]:
                            image = ImageOps.autocontrast(ImageOps.grayscale(image))
                        parsed = await read_frame(
                            engine,
                            image,
                            focus=True,
                            require_current=tracker.needs_current(time.monotonic()),
                            rank_image=rank_image,
                            budget=FrameBudget(),
                            previous_new=tracker.pending_new_key(),
                            variant_mismatch=catalog.variant_mismatch,
                            validate_stats=catalog.plausible_stats,
                        )
                        tracked = tracker.update(parsed, time.monotonic())
                        if tracked["newRollWarning"]:
                            tracked["newRollWarning"] = {
                                **tracked["newRollWarning"],
                                "id": f"{revision}:{tracked['newRollWarning']['id']}",
                            }
                        last_cards = tracked["cards"]
                        self._publish(
                            revision,
                            **tracked,
                            error=None,
                            captureBackend=camera.backend,
                            scanMs=round((time.monotonic() - started) * 1000),
                        )
                        del image, rank_image
                    except CapturePending as exc:
                        # Keep the new duplicator alive long enough to receive
                        # its first frame. No pixels means no OCR observation.
                        retry_at = time.monotonic() + 0.05
                        self._publish(
                            revision,
                            status="waiting",
                            error=str(exc),
                            errorKind="capture",
                            newRollWarning=None,
                            cards=[
                                {**card, "snapshot": True, "displayStale": True}
                                for card in last_cards
                            ],
                        )
                    except Exception as exc:
                        self._record_error(stage, exc)
                        if camera:
                            camera.close()
                            camera = None
                        if isinstance(exc, CaptureUnavailable):
                            tracker.interrupt()
                        else:
                            tracker = RollTracker(tracker.stat_warning)
                        if engine:
                            reset_layout(engine)
                        retry_at, check_at = time.monotonic() + 1, 0
                        self._publish(
                            revision,
                            status="error",
                            error=str(exc),
                            errorKind=stage,
                            newRollWarning=None,
                            cards=[
                                {**card, "snapshot": True, "displayStale": True}
                                for card in last_cards
                            ],
                        )
                    self._wake.wait(
                        max(0.015, config["interval"] - (time.monotonic() - started))
                    )
                    self._wake.clear()
        finally:
            if camera:
                camera.close()

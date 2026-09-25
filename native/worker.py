# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Native screen worker, communicating through Qt signals rather than a server."""

import asyncio
import copy
import threading
import time

import mss
from PIL import ImageOps
from PySide6.QtCore import QThread, Signal

from capture import DesktopCapture, monitor_identity
from ocr_engine import LocalOCR
from reader import read_frame, reset_layout
from tracker import RollTracker
from preferences import capture_settings


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

    def configure(self, settings, running):
        with self._guard:
            self._config = {**capture_settings(settings), "running": bool(running)}
            self._revision += 1
            revision = self._revision
        self._wake.set()
        return revision

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

    def run(self):
        try:
            asyncio.run(self._loop())
        except Exception as exc:
            _, revision = self._snapshot()
            self._publish(
                revision, status="error", cards=[], newRollWarning=None, error=str(exc)
            )

    async def _loop(self):
        engine = camera = None
        monitors, prior_revision = [], -1
        tracker = RollTracker()
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
                            self._publish(
                                revision,
                                status="error",
                                error=str(exc),
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
                            if monitors:
                                index = config["monitor"] - 1
                                identity = (
                                    monitor_identity(monitors[index])
                                    if index < len(monitors)
                                    else None
                                )
                                matches = [
                                    i + 1
                                    for i, m in enumerate(refreshed)
                                    if monitor_identity(m) == identity
                                ]
                                with self._guard:
                                    if revision == self._revision:
                                        self._config["monitor"] = (
                                            matches[0] if len(matches) == 1 else 1
                                        )
                                        if len(matches) != 1:
                                            self._config["running"] = False
                                        self._revision += 1
                            monitors = refreshed
                            config, revision = self._snapshot()
                            if config["monitor"] > len(monitors):
                                revision = self.configure(
                                    {**config, "monitor": 1}, False
                                )
                                config, revision = self._snapshot()
                            self.displays.emit(
                                monitors, {**config, "revision": revision}
                            )
                    if revision != prior_revision:
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
                    try:
                        if engine is None:
                            engine = LocalOCR()
                        if camera is None:
                            camera = DesktopCapture(
                                screen,
                                monitors[config["monitor"] - 1],
                                config["backend"],
                            )
                        image = camera.grab(config["region"])
                        rank_image = image
                        if config["contrast"]:
                            image = ImageOps.autocontrast(ImageOps.grayscale(image))
                        parsed = await read_frame(
                            engine,
                            image,
                            focus=True,
                            require_current=tracker.needs_current(time.monotonic()),
                            rank_image=rank_image,
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
                    except Exception as exc:
                        if camera:
                            camera.close()
                            camera = None
                        tracker = RollTracker()
                        if engine:
                            reset_layout(engine)
                        retry_at, check_at = time.monotonic() + 1, 0
                        self._publish(
                            revision,
                            status="error",
                            error=str(exc),
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

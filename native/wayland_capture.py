# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""User-authorized portal screen sharing; no XWayland screenshot fallback."""

import ctypes
import os
import threading

from PIL import Image
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import (
    QMediaCaptureSession,
    QScreenCapture,
    QVideoFrame,
    QVideoSink,
)

from capture import CapturePending, CaptureStopped, capture_box, validate_frame
from wayland_portal import PortalWatch


def validate_capture_environment():
    """Reject Qt overrides and missing PipeWire instead of an XWayland fallback."""
    if (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11"
        or os.environ.get("QT_SCREEN_CAPTURE_BACKEND", "").strip()
        or os.environ.get("QT_MEDIA_BACKEND", "ffmpeg").lower() not in ("", "ffmpeg")
    ):
        raise CaptureStopped(
            "Wayland sharing requires Qt's automatic screen capture and FFmpeg backend. "
            "Remove conflicting Qt or X11 session overrides, then restart RivenLens."
        )
    try:
        ctypes.CDLL("libpipewire-0.3.so.0")
    except OSError as exc:
        raise CaptureStopped(
            "PipeWire's client library is unavailable. Install your desktop's "
            "PipeWire packages, then restart RivenLens."
        ) from exc


def create_screen_stream(parent):
    """Qt's FFmpeg backend negotiates ScreenCast permission and PipeWire."""
    session = QMediaCaptureSession(parent)
    try:
        capture = QScreenCapture(session)
        if capture.error() != QScreenCapture.Error.NoError:
            raise CaptureStopped("Qt's screen-sharing backend is unavailable.")
        sink = QVideoSink(session)
        session.setScreenCapture(capture)
        session.setVideoSink(sink)
        return session, capture, sink
    except Exception:
        session.deleteLater()
        raise


def frame_image(frame):
    """Convert sampled pixels only, preserving stride and presentation orientation."""
    pixels = frame.toImage().convertToFormat(QImage.Format.Format_RGB888)
    if pixels.isNull():
        raise CapturePending("Waiting for a readable screen-sharing frame.")
    image = Image.frombytes(
        "RGB",
        (pixels.width(), pixels.height()),
        pixels.constBits().tobytes(),
        "raw",
        "RGB",
        pixels.bytesPerLine(),
    )
    # toImage handles the surface format, but not per-frame presentation metadata.
    rotation = frame.rotation().value
    if rotation:
        image = image.transpose(
            {
                90: Image.Transpose.ROTATE_270,
                180: Image.Transpose.ROTATE_180,
                270: Image.Transpose.ROTATE_90,
            }[rotation]
        )
    if frame.mirrored():
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return image


class WaylandSource(QObject):
    """GUI-thread session lifecycle, with one latest frame for the OCR thread."""

    _running_requested = Signal(bool)
    _close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._guard = threading.Lock()
        self._requested = self._closed = self._running = self._was_active = False
        self._generation = self._sequence = 0
        self._frame = self._error = None
        self._session = self._capture = self._sink = None
        self._portal = None
        self._authorized = False
        self._portal_wait = QTimer(self)
        self._portal_wait.setSingleShot(True)
        self._portal_wait.setInterval(5000)
        self._portal_wait.timeout.connect(self._sharing_not_started)
        self._connections = []
        self._running_requested.connect(self._set_running)
        self._close_requested.connect(self._close)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def monitors(self):
        # A stable identity avoids resets when the selected stream changes size.
        # The portal, not QScreen or MSS, selects the physical monitor.
        return [
            {
                "left": 0,
                "top": 0,
                "width": 0,
                "height": 0,
                "unique_id": "wayland-portal",
                "portal": True,
            }
        ]

    def open(self, monitor, backend):
        return SharedScreenCapture(self)

    def set_running(self, running):
        self._running_requested.emit(bool(running))

    def close(self):
        self._close_requested.emit()

    @Slot(bool)
    def _set_running(self, running):
        if self._closed or running == self._requested:
            return
        self._requested = running
        self._release()
        if not running:
            return
        with self._guard:
            self._running, self._error = True, None
        try:
            validate_capture_environment()
            self._portal = PortalWatch()
            self._portal.setParent(self)
            self._portal.stopped.connect(self._portal_stopped)
            self._portal.ready.connect(self._start_stream)
            self._portal.authorized.connect(self._portal_authorized)
            self._portal.prepare()
        except CaptureStopped as exc:
            self._fail(str(exc))
        except Exception:
            self._fail(
                "The desktop session bus is unavailable. Restart RivenLens in a Wayland desktop session."
            )

    @Slot()
    def _start_stream(self):
        if self.sender() != self._portal:
            return
        with self._guard:
            if not self._running:
                return
            generation = self._generation
        try:
            self._session, self._capture, self._sink = create_screen_stream(self)

            # Direct delivery replaces one shared frame even if the GUI is busy;
            # no queued full-frame backlog or conversion at monitor refresh rate.
            def receive(frame):
                self._receive_frame(frame, generation)

            self._connections = [
                self._sink.videoFrameChanged.connect(
                    receive, Qt.ConnectionType.DirectConnection
                ),
                self._capture.errorOccurred.connect(self._failed),
                self._capture.activeChanged.connect(self._active_changed),
            ]
            self._portal_wait.start()
            self._capture.start()
        except Exception:
            self._fail(
                "Screen sharing could not start. Check PipeWire, the desktop's "
                "ScreenCast portal and Qt's FFmpeg backend, then press Start OCR."
            )

    @Slot(str)
    def _portal_stopped(self, message):
        if self.sender() == self._portal:
            self._fail(message)

    @Slot()
    def _portal_authorized(self):
        if self.sender() == self._portal:
            # Qt may fall back internally; never accept pixels without the
            # successful Start response for our own ScreenCast session.
            with self._guard:
                self._authorized = self._running
            self._portal_wait.stop()

    @Slot()
    def _sharing_not_started(self):
        if self._portal is not None and not self._portal.session:
            self._fail(
                "The screen-sharing portal did not create a session. Check your "
                "desktop's ScreenCast backend, then press Start OCR to try again."
            )

    @Slot()
    def _close(self):
        self._closed = True
        self._requested = False
        self._release()

    def _release(self):
        self._portal_wait.stop()
        with self._guard:
            self._running = False
            self._authorized = False
            self._frame = None
            self._generation += 1
        for connection in self._connections:
            QObject.disconnect(connection)
        self._connections.clear()
        self._was_active = False
        if self._portal is not None:
            self._portal.close()
            self._portal.deleteLater()
            self._portal = None
        capture, session = self._capture, self._session
        self._capture = self._session = self._sink = None
        if capture is not None:
            capture.stop()
        if session is not None:
            session.setVideoSink(None)
            session.setScreenCapture(None)
            session.deleteLater()

    def _receive_frame(self, frame, generation):
        # Runs on the producer thread. Only the shared buffer is touched here.
        with self._guard:
            if self._running and self._authorized and generation == self._generation:
                self._frame = QVideoFrame(frame) if frame.isValid() else None
                self._sequence += 1

    @Slot(QScreenCapture.Error, str)
    def _failed(self, error, message):
        if self.sender() != self._capture or error == QScreenCapture.Error.NoError:
            return
        self._fail(
            "Screen sharing ended or permission was declined. Check PipeWire and "
            "the desktop's ScreenCast portal, then press Start OCR to try again."
            + (f" {message[:500]}" if message else "")
        )

    @Slot(bool)
    def _active_changed(self, active):
        if self.sender() != self._capture:
            return
        if active:
            self._was_active = True
        elif self._was_active:
            self._fail(
                "Screen sharing ended. Press Start OCR to choose a screen again."
            )

    def _fail(self, message):
        self._release()
        with self._guard:
            self._error = message

    def snapshot(self):
        with self._guard:
            if self._error:
                raise CaptureStopped(self._error)
            if not self._running or self._frame is None:
                raise CapturePending("Choose a screen in the screen-sharing dialog.")
            return QVideoFrame(self._frame), (self._generation, self._sequence)

    def validate_session(self, generation):
        with self._guard:
            if self._error:
                raise CaptureStopped(self._error)
            if not self._running or generation != self._generation:
                raise CapturePending("Screen sharing is paused.")


class SharedScreenCapture:
    """A reconfigurable OCR view; closing it does not reopen the sharing dialog."""

    backend = "wayland"

    def __init__(self, source):
        self.source = source
        self._key = self._image = None

    def close(self):
        self._key = self._image = None

    def check(self):
        self.source.validate_session(self._key[0])

    def grab(self, region):
        frame, key = self.source.snapshot()
        if key != self._key:
            self._image = frame_image(frame)
            self._key = key
        # Use stream pixels, not compositor logical dimensions (fractional DPI).
        box = capture_box(
            {
                "left": 0,
                "top": 0,
                "width": self._image.width,
                "height": self._image.height,
            },
            region,
        )
        image = self._image.crop(
            (
                box["left"],
                box["top"],
                box["left"] + box["width"],
                box["top"] + box["height"],
            )
        )
        self.check()
        return validate_frame(image)

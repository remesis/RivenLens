# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Desktop capture selection, separate from the OCR worker and its settings."""

import mss

from capture import DesktopCapture, desktop_supported, is_wayland


def create_capture_source(parent):
    if is_wayland():
        # Qt Multimedia is only needed for permission-based Linux sharing.
        from wayland_capture import WaylandSource

        return WaylandSource(parent)
    return DesktopSource()


class DesktopSource:
    """MSS resources are created and released on the worker thread."""

    def __enter__(self):
        desktop_supported()
        self.screen = mss.MSS()
        return self

    def __exit__(self, *_):
        self.screen.close()

    def monitors(self):
        keys = ("left", "top", "width", "height", "unique_id", "name", "is_primary")
        with mss.MSS() as probe:
            return [{k: m[k] for k in keys if k in m} for m in probe.monitors[1:]]

    def open(self, monitor, backend):
        return DesktopCapture(self.screen, monitor, backend)

    def set_running(self, running):
        """The worker gates synchronous grabs; there is no separate producer."""

    def close(self):
        """Only __exit__ releases MSS, on its owning worker thread."""

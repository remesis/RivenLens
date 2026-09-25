# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""Monitor pixels through Windows desktop APIs, with no application handles."""

from PIL import Image


def capture_box(monitor, region):
    """Convert percentage edges to bounded physical desktop pixels."""
    x, y, w, h = region
    width, height = monitor["width"], monitor["height"]
    left = max(0, min(width - 1, round(width * x / 100)))
    top = max(0, min(height - 1, round(height * y / 100)))
    right = max(left + 1, min(width, round(width * (x + w) / 100)))
    bottom = max(top + 1, min(height, round(height * (y + h) / 100)))
    return {
        "left": monitor["left"] + left,
        "top": monitor["top"] + top,
        "width": right - left,
        "height": bottom - top,
    }


def monitor_identity(monitor):
    return monitor.get("unique_id") or tuple(
        monitor[key] for key in ("left", "top", "width", "height")
    )


def matching_output(outputs, monitor):
    """Never assume DXGI output order matches Windows monitor enumeration."""
    expected = (
        monitor["left"],
        monitor["top"],
        monitor["left"] + monitor["width"],
        monitor["top"] + monitor["height"],
    )
    matches = []
    for device, output in outputs:
        rect = output.desc.DesktopCoordinates
        if (
            output.attached_to_desktop
            and (rect.left, rect.top, rect.right, rect.bottom) == expected
        ):
            matches.append((device, output))
    if len(matches) != 1:
        raise RuntimeError("The selected monitor could not be identified uniquely.")
    return matches[0]


def create_desktop_camera(monitor):
    # Lazy imports: paused sessions do not create a desktop duplication resource.
    # This adapter is pinned to DXcam 0.3.0. Fresh enumeration handles hot-plugging
    # without relying on its module-global discovery cache.
    from dxcam import DXCamera
    from dxcam.core import Device, Output
    from dxcam.util.io import enum_dxgi_adapters

    outputs = []
    for adapter in enum_dxgi_adapters():
        device = Device(adapter)
        outputs.extend((device, Output(raw)) for raw in device.enum_outputs())
    device, output = matching_output(outputs, monitor)
    return DXCamera(
        output=output,
        device=device,
        region=None,
        output_color="RGB",
        backend="dxgi",
        processor_backend="numpy",
        max_buffer_len=2,
    )


class DesktopCapture:
    """A single selected monitor, synchronous grabs and explicit resource release."""

    def __init__(self, screen, monitor, backend="auto"):
        self.screen = screen
        self.monitor = dict(monitor)
        self.camera = None
        self.backend = "gdi"
        self.fallback = False
        if backend != "gdi":
            try:
                self.camera = create_desktop_camera(monitor)
                self.backend = "dxgi"
            except Exception:
                if backend == "dxgi":
                    raise RuntimeError(
                        "Desktop duplication is unavailable. Try Compatibility capture."
                    ) from None
                self.fallback = True

    def close(self):
        camera, self.camera = self.camera, None
        if camera is not None:
            try:
                camera.release()
            except Exception:
                # A disconnected display may already have invalidated its handles.
                pass

    def grab(self, region):
        box = capture_box(self.monitor, region)
        if self.camera is not None:
            # Desktop duplication includes fullscreen surfaces. These are Windows'
            # monitor pixels, not a game hook, game process or game texture read.
            frame = self.camera.grab(new_frame_only=False)
            if frame is None:
                raise RuntimeError("Waiting for display pixels after a mode change.")
            if frame.shape[:2] != (self.monitor["height"], self.monitor["width"]):
                raise RuntimeError("Display size changed. Refreshing capture.")
            left = box["left"] - self.monitor["left"]
            top = box["top"] - self.monitor["top"]
            image = Image.fromarray(frame).crop(
                (left, top, left + box["width"], top + box["height"])
            )
        else:
            shot = self.screen.grab(box)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if max(image.size) > 16 and max(high for _, high in image.getextrema()) < 5:
            raise RuntimeError(
                "Capture is black. Check the selected monitor or try another capture method."
            )
        return image

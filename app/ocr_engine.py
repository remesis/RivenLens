# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""On-device Windows OCR for ordinary in-memory images."""

import re
import math

from PIL import Image

from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter


class LocalOCR:
    def __init__(self):
        self.engine = OcrEngine.try_create_from_language(Language("en-US"))
        if self.engine is None:
            raise RuntimeError(
                "Windows English OCR is not installed. Add English language OCR in Windows Settings."
            )

    async def read(self, image):
        original_size = image.size
        scale = min(1, OcrEngine.max_image_dimension / max(image.size))
        if scale < 1:
            image = image.resize(
                (
                    max(1, round(image.width * scale)),
                    max(1, round(image.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        sx, sy = image.width / original_size[0], image.height / original_size[1]
        image = image.convert("RGBA")
        writer = DataWriter()
        writer.write_bytes(image.tobytes("raw", "BGRA"))
        buffer = writer.detach_buffer()
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            buffer, BitmapPixelFormat.BGRA8, image.width, image.height
        )
        try:
            result = await self.engine.recognize_async(bitmap)
            angle = math.radians(result.text_angle or 0)

            def original_box(x, y, width, height):
                # Windows OCR boxes are expressed in its rotated text plane.
                # Return them to captured pixels before cropping or reading pips.
                cx, cy = image.width / 2, image.height / 2
                corners = [
                    (
                        cx + (px - cx) * math.cos(angle) - (py - cy) * math.sin(angle),
                        cy + (px - cx) * math.sin(angle) + (py - cy) * math.cos(angle),
                    )
                    for px in (x, x + width)
                    for py in (y, y + height)
                ]
                left, right = min(p[0] for p in corners), max(p[0] for p in corners)
                top, bottom = min(p[1] for p in corners), max(p[1] for p in corners)
                return {
                    "x": left / sx,
                    "y": top / sy,
                    "w": (right - left) / sx,
                    "h": (bottom - top) / sy,
                }

            lines = []
            for line in result.lines:
                words = list(line.words)
                if not words:
                    continue
                x = min(w.bounding_rect.x for w in words)
                y = min(w.bounding_rect.y for w in words)
                right = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
                bottom = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
                entry = {
                    "text": line.text,
                    **original_box(x, y, right - x, bottom - y),
                }
                # Decorative footer arrows can inflate the whole line's box.
                # Use the MR word's font height to anchor the rank-pip row.
                mr = next(
                    (w for w in words if re.fullmatch(r"MR[0-9IOil]*", w.text, re.I)),
                    None,
                )
                if mr:
                    rect = mr.bounding_rect
                    entry["footerBounds"] = original_box(
                        rect.x, rect.y, rect.width, rect.height
                    )
                lines.append(entry)
            return lines
        finally:
            bitmap.close()
            writer.close()

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
from localization import LANGUAGES, adapt_lines, profile, repair_decimal_spacing


def available_languages():
    """Report exact installed recognizers, without installing or changing Windows."""
    return {
        language.language_tag.casefold()
        for language in OcrEngine.available_recognizer_languages
    }


def recognizer_tag(language, available):
    requested = LANGUAGES[language]["recognizer"]
    if requested.casefold() in available:
        return requested
    # Some Windows models expose a neutral BCP-47 tag (notably Russian's ru).
    # Chinese script and Brazilian Portuguese distinctions must remain explicit.
    alternatives = {
        "zh": ("zh-hans-cn", "zh-hans"),
        "tc": ("zh-hant-tw", "zh-hant"),
        "pt": (),
    }.get(language, (requested.split("-")[0].casefold(),))
    if match := next((tag for tag in alternatives if tag in available), None):
        return match
    # English spelling varies but the game text does not. Other game locales
    # (including Brazil/Portugal and Simplified/Traditional Chinese) stay exact.
    if language == "en":
        return next((tag for tag in sorted(available) if tag.startswith("en-")), None)
    return None


class LocalOCR:
    def __init__(self, language="en"):
        if language not in LANGUAGES:
            raise ValueError("Unsupported game language")
        self.language = language
        tag = recognizer_tag(language, available_languages())
        self.engine = OcrEngine.try_create_from_language(Language(tag)) if tag else None
        if self.engine is None:
            raise RuntimeError(
                f"Windows OCR for {LANGUAGES[language]['label']} is not installed. "
                "Add this language's Basic typing and Optical character recognition "
                "features in Windows Settings > Time & language > Language options, "
                "then restart OCR. Your Windows display language can stay unchanged."
            )

    async def read_ui_fallback(self, image):
        """Optional Latin-font fallback for tiny action captions, not stat values.

        Some Windows language models systematically misread the game's uppercase
        CONFIRMAR font. A second installed recognizer can read those same pixels;
        the result must still match the chosen game's exact localized UI wording.
        """
        if self.language not in ("de", "fr", "it", "pl", "es", "pt", "tr"):
            return []
        if not hasattr(self, "_ui_reader"):
            try:
                self._ui_reader = LocalOCR("en")
            except RuntimeError:
                self._ui_reader = None
        if self._ui_reader is None:
            return []
        return adapt_lines(await self._ui_reader.read(image), self.language)

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
                if self.language != "en":
                    located_words = [
                        {
                            "text": word.text,
                            **original_box(
                                word.bounding_rect.x,
                                word.bounding_rect.y,
                                word.bounding_rect.width,
                                word.bounding_rect.height,
                            ),
                        }
                        for word in words
                    ]
                    if self.language == "ru":
                        repaired = repair_decimal_spacing(line.text, located_words)
                        if repaired != line.text:
                            entry["text"] = repaired
                            entry["localizedNumberJoin"] = True
                    footer = profile(self.language).footer_bounds(located_words)
                    if footer:
                        entry["footerBounds"] = footer
                lines.append(entry)
            return adapt_lines(lines, self.language)
        finally:
            bitmap.close()
            writer.close()

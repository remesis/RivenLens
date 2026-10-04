# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Local CPU recognition. Runtime setup is separate from screen inference."""

import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import re
import threading

import numpy as np

from localization import (
    LATIN_UI_LANGUAGES,
    adapt_lines,
    repair_decimal_spacing,
    roll_counter_bounds,
)
from ocr_models import (
    activate_runtime,
    model_directory,
    model_path,
    selection,
    valid_model,
)
from rapid_text import caption_namespace, frame_scope, recognition_profile
from rapid_layout import (
    companion_titles,
    contextual_titles,
    join_names,
    normalize_controls,
    observe_captions,
    split_columns,
)

_INITIALIZE = threading.Lock()


def rectangle(points):
    points = np.asarray(points, dtype=float)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise RuntimeError("Invalid OCR bounding polygon")
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    if (
        right <= left
        or bottom <= top
        or not np.isfinite((right - left, bottom - top)).all()
    ):
        raise RuntimeError("Invalid OCR bounding polygon")
    return {
        "x": float(left),
        "y": float(top),
        "w": float(right - left),
        "h": float(bottom - top),
    }


def same_row(left, right):
    height = min(left["h"], right["h"])
    if height <= 0:
        return False
    overlap = min(left["y"] + left["h"], right["y"] + right["h"]) - max(
        left["y"], right["y"]
    )
    centers = abs(left["y"] + left["h"] / 2 - right["y"] - right["h"] / 2)
    gap = max(left["x"], right["x"]) - min(
        left["x"] + left["w"], right["x"] + right["w"]
    )
    return overlap >= height * 0.5 and centers <= height * 0.4 and gap <= height * 3


def group_lines(pieces):
    """Join neighboring detector boxes without inventing missing characters."""
    groups = []
    for piece in sorted(
        pieces, key=lambda item: (item["y"] + item["h"] / 2, item["x"])
    ):
        candidates = [
            group
            for group in groups
            if any(same_row(member, piece) for member in group)
        ]
        if candidates:
            min(
                candidates,
                key=lambda group: min(abs(item["x"] - piece["x"]) for item in group),
            ).append(piece)
        else:
            groups.append([piece])
    lines = []
    numeric = re.compile(r"[+\-−]?\d*(?:[.,]\d*)?%?")
    for group in groups:
        ordered = sorted(group, key=lambda item: item["x"])
        text = ordered[0]["text"]
        for previous, current in zip(ordered, ordered[1:]):
            gap = current["x"] - previous["x"] - previous["w"]
            touching_number = (
                gap <= min(previous["h"], current["h"]) * 0.25
                and numeric.fullmatch(previous["text"])
                and numeric.fullmatch(current["text"])
                and any(c.isdigit() for c in previous["text"] + current["text"])
            )
            text += ("" if touching_number else " ") + current["text"]
        left = min(item["x"] for item in ordered)
        top = min(item["y"] for item in ordered)
        right = max(item["x"] + item["w"] for item in ordered)
        bottom = max(item["y"] + item["h"] for item in ordered)
        scores = [
            item["confidence"] for item in ordered if item["confidence"] is not None
        ]
        lines.append(
            {
                "text": text,
                "x": left,
                "y": top,
                "w": right - left,
                "h": bottom - top,
                "glyphHeight": min(item["h"] for item in ordered),
                "confidence": min(scores) if scores else None,
                "rawPieces": ordered,
                "words": [word for item in ordered for word in item["words"]],
            }
        )
    return sorted(lines, key=lambda item: (item["y"], item["x"]))


def make_engine(language, *, caption=False):
    activate_runtime()
    from rapidocr import (
        EngineType,
        LangRec,
        ModelType,
        OCRVersion,
        RapidOCR as Recognizer,
    )
    from rapidocr.utils.download_file import DownloadFile

    det, rec, script = (
        ("caption-det", "caption-rec", "en") if caption else selection(language)
    )
    keys = (det, rec, "cls")
    if not all(valid_model(key) for key in keys):
        raise RuntimeError(
            "Local OCR models are missing or damaged. Choose Start OCR to set them up."
        )
    params = {
        "Global.model_root_dir": str(model_directory()),
        "Global.use_cls": False,
        "Global.return_word_box": True,
        "Global.log_level": "error",
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.ocr_version": OCRVersion.PPOCRV5 if caption else OCRVersion.PPOCRV6,
        "Det.model_type": ModelType.MOBILE if caption else ModelType.SMALL,
        "Det.model_path": str(model_path(det)),
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.ocr_version": OCRVersion.PPOCRV6 if rec == "rec" else OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.SMALL if rec == "rec" else ModelType.MOBILE,
        "Rec.lang_type": LangRec(script),
        "Rec.model_path": str(model_path(rec)),
        "Cls.model_path": str(model_path("cls")),
        "EngineConfig.onnxruntime.intra_op_num_threads": 4,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "EngineConfig.onnxruntime.use_cuda": False,
        "EngineConfig.onnxruntime.use_dml": False,
    }

    def offline(*args, **kwargs):
        raise RuntimeError(
            "OCR cannot download files during recognition. Use OCR setup instead."
        )

    # Initialization is serialized: the dependency's downloader cannot run even
    # accidentally when all explicitly supplied local files should suffice.
    with _INITIALIZE:
        original = DownloadFile.__dict__["run"]
        DownloadFile.run = classmethod(offline)
        try:
            engine = Recognizer(params=params)
        finally:
            DownloadFile.run = original
    for component in (engine.text_det, engine.text_rec):
        if component.session.session.get_providers()[0] != "CPUExecutionProvider":
            raise RuntimeError("OCR did not initialize its CPU provider.")
    return engine


class RapidOCR:
    backend = "rapid"
    frame_scope = staticmethod(frame_scope)

    def __init__(self, language="en"):
        self.language = language
        self.profile = recognition_profile(language)
        self.automatic_rank = True
        self._engine = make_engine(language)
        self._caption_engine = None
        self._lock = threading.RLock()
        self._closed = False
        self._cache = OrderedDict()
        self._cache_bytes = 0

    def close(self):
        with self._lock:
            self._closed = True
            self._engine = self._caption_engine = None
            self._cache.clear()
            self._cache_bytes = 0

    async def read(self, image):
        return await asyncio.to_thread(self._read, image)

    async def read_ui_fallback(self, image):
        if self.language not in LATIN_UI_LANGUAGES:
            return []
        # Latin card/UI recognition uses the same models. Reuse their sessions,
        # with the English label profile and the denser UI pass when needed.
        return adapt_lines(
            await asyncio.to_thread(self._read, image, False, True), self.language
        )

    async def read_caption(self, image):
        return await asyncio.to_thread(self._read, image, True)

    def _read(self, image, caption=False, ui_fallback=False):
        with self._lock:
            if self._closed:
                raise RuntimeError("OCR engine is closed")
            pixels = image.convert("RGB")
            # Small footer glyphs/Cyrillic and the UI fallback need the denser
            # detection pass. Manual-rank cards otherwise use the cheaper floor.
            floor = (
                736
                if ui_fallback or self.automatic_rank or self.language in ("ru", "uk")
                else 512
            )
            profile = recognition_profile("en") if ui_fallback else self.profile
            key = (
                caption,
                ui_fallback,
                floor,
                caption_namespace(),
                pixels.size,
                hashlib.blake2b(pixels.tobytes(), digest_size=16).digest(),
            )
            if key in self._cache:
                self._cache.move_to_end(key)
                observe_captions(self._cache[key][1], profile)
                return deepcopy(self._cache[key][1])
            if caption:
                if self._caption_engine is None:
                    self._caption_engine = make_engine(self.language, caption=True)
                engine = self._caption_engine
            else:
                engine = self._engine
            previous_floor = engine.text_det.limit_side_len
            previous_batch = engine.text_rec.rec_batch_num
            try:
                if not caption:
                    engine.text_det.limit_side_len = floor
                    engine.text_rec.rec_batch_num = 6
                result = engine(pixels, use_cls=False, return_word_box=True)
            finally:
                engine.text_det.limit_side_len = previous_floor
                engine.text_rec.rec_batch_num = previous_batch
            lines = self._lines(result, profile)
            # Pixel size is a conservative work budget, not retained image data.
            # Copies keep parser annotations out of the cached evidence.
            cost = pixels.width * pixels.height * 3 + len(repr(lines).encode("utf-8"))
            if cost <= 40 * 1024 * 1024:
                self._cache[key] = (cost, deepcopy(lines))
                self._cache_bytes += cost
                while len(self._cache) > 64 or self._cache_bytes > 40 * 1024 * 1024:
                    _, (removed, _) = self._cache.popitem(last=False)
                    self._cache_bytes -= removed
            return lines

    def _lines(self, result, profile=None):
        profile = profile or self.profile
        language = profile.language
        if result.txts is None:
            return []
        pieces = []
        word_results = result.word_results or ()
        aligned = len(word_results) == len(result.txts)
        scores = getattr(result, "scores", ()) or ()
        for index, (text, points) in enumerate(zip(result.txts, result.boxes)):
            words = []
            if aligned and word_results[index]:
                for word_text, score, polygon in word_results[index]:
                    if polygon is not None:
                        try:
                            bounds = rectangle(polygon)
                        except (RuntimeError, ValueError, TypeError):
                            continue
                        words.append(
                            {
                                "text": word_text,
                                "confidence": float(score),
                                **bounds,
                            }
                        )
            pieces.append(
                {
                    "text": text,
                    **rectangle(points),
                    "words": words,
                    "confidence": float(scores[index]) if index < len(scores) else None,
                }
            )
        lines = []
        for grouped in group_lines(pieces):
            entry = {key: value for key, value in grouped.items() if key != "words"}
            text, words = entry["text"], grouped["words"]
            mr = next(
                (
                    word
                    for word in words
                    if re.fullmatch(r"MR[0-9IOil]*", word["text"], re.I)
                ),
                None,
            )
            if mr:
                entry["footerBounds"] = {key: mr[key] for key in ("x", "y", "w", "h")}
            if language != "en":
                if language == "ru":
                    repaired = repair_decimal_spacing(text, words)
                    if repaired != text:
                        entry["text"] = repaired
                        entry["localizedNumberJoin"] = True
                footer = profile.footer_bounds(words)
                if footer:
                    entry["footerBounds"] = footer
                counter = roll_counter_bounds(text, words)
                if counter:
                    entry["counterBounds"] = counter
            lines.append(entry)
        lines = normalize_controls(lines, profile)
        lines = split_columns(lines, profile)
        lines = join_names(lines, profile)
        observe_captions(lines, profile)
        lines = contextual_titles(lines, profile)
        lines = companion_titles(lines, profile)
        return adapt_lines(lines, language, locale=profile)

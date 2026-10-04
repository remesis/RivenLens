# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Whole-label recognition tolerances for the local neural recognizer.

These are script-specific glyph ambiguities, not edit-distance matching. A
complete known template must match uniquely, with its observed quantity intact.
Windows OCR continues to use its original, stricter language profiles.
"""

from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
import re
import unicodedata

from localization import control_key, name_key, profile, text_key

_CYRILLIC_VISUAL = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "C": "С",
        "E": "Е",
        "H": "Н",
        "K": "К",
        "M": "М",
        "O": "О",
        "P": "Р",
        "T": "Т",
        "X": "Х",
        "Y": "У",
        "a": "а",
        "c": "с",
        "e": "е",
        "o": "о",
        "p": "р",
        "x": "х",
        "y": "у",
    }
)
_UKRAINIAN = str.maketrans({"і": "и", "І": "И", "ї": "й", "Ї": "Й", "є": "е", "Є": "Е"})
_CAPTIONS = ContextVar("ocr_frame_captions", default=None)
_QUANTITY = re.compile(r"[+\-−]?(?:[xX×хХ]\s*)?\d+(?:[.,]\d+)?(?:\s*[xX×хХmMмМsSсС%])?")


@contextmanager
def frame_scope():
    """Caption evidence belongs to this captured frame, never a previous roll."""
    token = _CAPTIONS.set(set())
    try:
        yield
    finally:
        _CAPTIONS.reset(token)


def observe_caption(caption):
    current = _CAPTIONS.get()
    if current is not None and caption:
        current.add(caption)


def caption_namespace():
    return tuple(sorted(_CAPTIONS.get() or ()))


def russian_label(text):
    """Interpret visual letter shapes without changing quantities or their units."""
    result, cursor = [], 0
    for match in _QUANTITY.finditer(text):
        result.extend(
            (text[cursor : match.start()].translate(_CYRILLIC_VISUAL), match[0])
        )
        cursor = match.end()
    result.append(text[cursor:].translate(_CYRILLIC_VISUAL))
    return "".join(result)


def label_fold(text, language):
    if language == "ja":
        return text.replace("卜", "ト").replace("擊", "撃")
    if language == "tr":
        return text.replace("i\u0307", "i").replace("ı", "i")
    if language == "uk":
        return text.translate(_UKRAINIAN)
    return text


def weapon_key(text, language):
    if language in ("ru", "uk"):
        text = text.translate(_CYRILLIC_VISUAL)
    if language == "uk":
        # The recognizer can assign a Latin n to the printed Cyrillic п. This
        # tolerance applies only to a complete, unique known weapon name.
        text = text.replace("n", "п").replace("N", "П").translate(_UKRAINIAN)
    return name_key(text)


class RecognitionProfile:
    def __init__(self, language):
        self.original = profile(language)
        self.language = language
        self.names = self.original.names
        self.aliases = {}
        for canonical, aliases in self.original.data["weapons"].items():
            plain = re.sub(r" \((Primary|Secondary|Rifle|Melee)\)$", "", canonical)
            for alias in aliases:
                observed_name = canonical if "(" in alias and ")" in alias else plain
                self.aliases.setdefault(weapon_key(alias, language), set()).add(
                    observed_name
                )
        self.templates = [
            (re.compile(label_fold(pattern.pattern, language), pattern.flags), stat)
            for pattern, stat in self.original.templates
        ]
        self.footer = re.compile(
            label_fold(self.original.footer.pattern, language),
            self.original.footer.flags,
        )

    def __getattr__(self, name):
        return getattr(self.original, name)

    def weapon(self, text):
        exact = self.original.weapon(text)
        if exact is not None or self.language not in ("ru", "uk"):
            return exact
        choices = self.aliases.get(weapon_key(text, self.language), set())
        return next(iter(choices)) if len(choices) == 1 else None

    def title_prefix(self, text):
        if self.original.title_prefix(text):
            return True
        key = weapon_key(text, self.language)
        return (
            self.language in ("ru", "uk")
            and len(key) >= 2
            and any(alias.startswith(key) for alias in self.aliases)
        )

    def title(self, text):
        exact = self.original.title(text)
        if exact is not None or self.language not in ("ru", "uk"):
            return exact
        candidates = []
        for match in re.finditer(r"\s+", text):
            prefix, suffix = text[: match.start()], text[match.end() :]
            choices = self.aliases.get(weapon_key(prefix, self.language), set())
            if (
                len(choices) == 1
                and len(suffix) >= 3
                and suffix[0].isalpha()
                and all(
                    c.isalpha() or unicodedata.category(c).startswith("M") or c in " -"
                    for c in suffix
                )
            ):
                candidates.append((len(prefix), next(iter(choices)), suffix))
        if not candidates:
            return None
        longest = max(row[0] for row in candidates)
        choices = {
            (canonical, suffix)
            for length, canonical, suffix in candidates
            if length == longest
        }
        return next(iter(choices)) if len(choices) == 1 else None

    def stat(self, text):
        exact = self.original.stat(text)
        if exact is not None:
            return exact
        if self.language == "ru":
            # A whole ordinary template must still validate the visually folded
            # label. Partial lines, unknown labels and quantities are not guessed.
            repaired = russian_label(text)
            if repaired != text:
                candidate = self.original.stat(repaired)
                if candidate is not None:
                    return candidate
        if self.language == "fr":
            match = re.fullmatch(
                r"\s*([+-]\d+(?:[.,]\d+)?)%\s+de\s+(?:Dégâte|Dégäte)\s*", text, re.I
            )
            if match:
                candidate = self.original.stat(match[1] + "% de Dégâts")
                if candidate == match[1] + "% Damage":
                    return candidate
        if self.language == "ko":
            repaired, count = re.subn(r"(치명(?:타|[eE])\s*확)를", r"\g<1>률", text)
            if count == 1:
                candidate = self.original.stat(repaired)
                if candidate and candidate.endswith(
                    (" Critical Chance", " Critical Chance for Slide Attack")
                ):
                    return candidate
            if re.match(r"^\s*8\s*[+-]\d", text):
                return self.original.stat(re.sub(r"^\s*8\s*", "", text, count=1))
        key = label_fold(text_key(text), self.language)
        choices = {}
        for pattern, stat in self.templates:
            match = pattern.fullmatch(key)
            if (
                match is None
                or stat["unit"] == "x"
                and bool(match["pre"]) == bool(match["post"])
            ):
                continue
            value = match["value"]
            choices[(stat["id"], value, stat["unit"])] = (
                f"{value}{stat['unit']} {stat['name']}"
            )
        return next(iter(choices.values())) if len(choices) == 1 else None

    def control(self, text):
        exact = self.original.control(text)
        if exact is not None or self.language not in ("ru", "uk"):
            return exact
        folded = text.translate(_CYRILLIC_VISUAL) if self.language == "ru" else text
        match = self.footer.match(label_fold(control_key(folded), self.language))
        return "MR " + match["level"] if match else None

    def footer_bounds(self, words):
        exact = self.original.footer_bounds(words)
        if exact is not None or self.language not in ("ru", "uk"):
            return exact
        for count in range(1, min(6, len(words)) + 1):
            prefix = words[:count]
            if (self.control(" ".join(w["text"] for w in prefix)) or "").startswith(
                "MR "
            ):
                anchor = next(
                    (w for w in prefix if re.fullmatch(r"[0-9IOil]+", w["text"])),
                    prefix[0],
                )
                return {k: anchor[k] for k in ("x", "y", "w", "h")}
        return None


@lru_cache(maxsize=15)
def recognition_profile(language):
    return RecognitionProfile(language)

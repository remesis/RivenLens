# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Local interface translations; never change canonical grading identities."""

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from string import Formatter

from PySide6.QtCore import QObject, QLocale, Signal
from PySide6.QtGui import QAction as QtAction
from PySide6.QtWidgets import (
    QCheckBox as QtCheckBox,
    QDialog as QtDialog,
    QFrame as QtFrame,
    QLabel as QtLabel,
    QPushButton as QtButton,
    QToolButton as QtToolButton,
)

from localization import LANGUAGES

DATA = json.loads((Path(__file__).parent / "data/ui_strings.json").read_text("utf-8"))


class InterfaceLanguage(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.code = "en"

    def set(self, code):
        if code not in LANGUAGES:
            raise ValueError("Unsupported interface language")
        if code != self.code:
            self.code = code
            self.changed.emit()


language = InterfaceLanguage()


def default_language(tags=None):
    """Use Windows' UI locale once, not whichever keyboard happens to be active."""
    if tags is None:
        locale = QLocale.system()
        tags = [*locale.uiLanguages(), locale.name()]
    for tag in tags:
        parts = tag.lower().replace("_", "-").split("-")
        if parts[0] == "zh":
            return "tc" if set(parts) & {"hant", "tw", "hk", "mo"} else "zh"
        if parts[0] in LANGUAGES:
            return parts[0]
    return "en"


@lru_cache(maxsize=13)
def tables(code):
    if code == "en":
        return {}, []
    column = DATA["languages"].index(code)
    messages = {key: values[column] for key, values in DATA["strings"].items()}
    patterns = []
    for key, value in messages.items():
        if "{" not in key:
            continue
        parts = []
        for literal, field, _, _ in Formatter().parse(key):
            parts.append(re.escape(literal))
            if field:
                value_pattern = (
                    r"[+−\-\d.,%×msk∞– /]+"
                    if field in ("number", "count", "low", "high")
                    else r".+?"
                )
                parts.append(r"(?P<" + field + ">" + value_pattern + ")")
        patterns.append((re.compile("".join(parts), re.S), value))
    patterns.sort(
        key=lambda row: len(row[0].pattern) - 15 * row[0].groups, reverse=True
    )
    # Short labels are derived from the game's own visible stat templates.
    messages.update(LANGUAGES[code]["statLabels"])
    messages.update(
        {name: aliases[0] for name, aliases in LANGUAGES[code]["weapons"].items()}
    )
    for key in ("Positive", "Negative", "Positive lock", "Negative lock"):
        messages[key.lower()] = messages[key]
    return messages, patterns


def translate(text, *, code=None, _depth=0):
    code = code or language.code
    if not isinstance(text, str) or not text or code == "en" or _depth > 5:
        return text
    messages, patterns = tables(code)
    # Only text nodes are translated; CSS, links and numeric HTML attributes
    # are never touched. Dynamic game text remains escaped at its source.
    if re.search(r"</?[a-zA-Z][^>]*>", text):
        return "".join(
            part
            if part.startswith("<")
            else html.escape(
                translate(html.unescape(part), code=code, _depth=_depth + 1),
                quote=False,
            )
            for part in re.split(r"(<[^>]*>)", text)
        )
    core = text.strip()
    if not core:
        return text
    translated = messages.get(core)
    if translated is None and core.startswith("(") and core.endswith(")"):
        translated = "(" + translate(core[1:-1], code=code, _depth=_depth + 1) + ")"
    if translated is None and core[:2] in ("+ ", "- ", "− "):
        translated = core[:2] + translate(core[2:], code=code, _depth=_depth + 1)
    if translated is None and core.endswith((":", ".")) and core[:-1] in messages:
        translated = messages[core[:-1]] + core[-1]
    if translated is None:
        for pattern, template in patterns:
            match = pattern.fullmatch(core)
            if match:
                translated = template.format(
                    **{
                        key: translate(value, code=code, _depth=_depth + 1)
                        for key, value in match.groupdict().items()
                    }
                )
                break
    if translated is None:
        for separator in ("\n", " · ", " + ", " or ", ": ", ", "):
            if separator in core:
                joiner = (
                    " " + messages.get("or", "or") + " "
                    if separator == " or "
                    else separator
                )
                translated = joiner.join(
                    translate(part, code=code, _depth=_depth + 1)
                    for part in core.split(separator)
                )
                break
    if translated is None:
        translated = core
    return (
        text[: len(text) - len(text.lstrip())] + translated + text[len(text.rstrip()) :]
    )


class TranslationMixin:
    """Keep English source text so repeated live switching is lossless."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ui_sources = {}
        language.changed.connect(self.retranslate)
        if args and isinstance(args[0], str) and hasattr(self, "text"):
            self.setText(args[0])

    def _set_ui(self, method, source):
        self._ui_sources[method] = source
        getattr(super(), method)(translate(source))

    def setText(self, text):
        self._set_ui("setText", text)

    def setToolTip(self, text):
        self._set_ui("setToolTip", text)

    def setWindowTitle(self, text):
        self._set_ui("setWindowTitle", text)

    def setAccessibleName(self, text):
        self._set_ui("setAccessibleName", text)

    def retranslate(self):
        for method, text in self._ui_sources.items():
            getattr(super(), method)(translate(text))


class QLabel(TranslationMixin, QtLabel):
    def clear(self):
        # Keep the source empty too, so a later language switch cannot restore
        # a planner message that was intentionally cleared.
        self.setText("")


class QPushButton(TranslationMixin, QtButton):
    pass


class QCheckBox(TranslationMixin, QtCheckBox):
    pass


class QToolButton(TranslationMixin, QtToolButton):
    pass


class QFrame(TranslationMixin, QtFrame):
    pass


class QDialog(TranslationMixin, QtDialog):
    pass


class QAction(TranslationMixin, QtAction):
    pass

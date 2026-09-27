# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Compact, keyboard-accessible language picker with original vector flag badges."""

from functools import lru_cache

from PySide6.QtCore import QByteArray, QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QWidget,
)

from widgets import Combo

NAMES = {
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "it": "Italiano",
    "ko": "한국어",
    "ru": "Русский",
    "ja": "日本語",
    "pl": "Polski",
    "es": "Español",
    "pt": "Português (BR)",
    "zh": "简体中文",
    "tc": "繁體中文",
    "tr": "Türkçe",
}


def flag_svg(code):
    # Flags identify the picker entries visually; the native language names
    # remain the accessible labels. No online images or emoji-font dependency.
    bars = {
        "de": ("#171717", "#d3232b", "#f7c844"),
        "ru": ("#fff", "#2256b7", "#da2737"),
        "es": ("#b92337", "#f9cb35", "#b92337"),
    }
    if code in bars:
        body = "".join(
            f'<rect y="{i * 8}" width="24" height="8" fill="{color}"/>'
            for i, color in enumerate(bars[code])
        )
    elif code in ("fr", "it"):
        colors = ("#1951ae" if code == "fr" else "#23925d", "#fff", "#db3547")
        body = "".join(
            f'<rect x="{i * 8}" width="8" height="24" fill="{color}"/>'
            for i, color in enumerate(colors)
        )
    elif code == "pl":
        body = '<rect width="24" height="24" fill="#fff"/><path d="M0 12h24v12H0z" fill="#db3547"/>'
    elif code == "ja":
        body = '<rect width="24" height="24" fill="#fff"/><circle cx="12" cy="12" r="6" fill="#bc2948"/>'
    elif code == "pt":
        body = '<rect width="24" height="24" fill="#19834c"/><path d="m2 12 10-7 10 7-10 7z" fill="#f7d34a"/><circle cx="12" cy="12" r="4.6" fill="#204d9f"/><path d="M7.5 10.5q5-1 9 3" stroke="#fff" stroke-width="1.1" fill="none"/>'
    elif code == "en":
        body = '<rect width="24" height="24" fill="#fff"/>'
        body += "".join(
            f'<rect y="{i * 24 / 13:.3f}" width="24" height="{24 / 13:.3f}" fill="#bd3345"/>'
            for i in range(0, 13, 2)
        )
        body += '<rect width="12.5" height="12.923" fill="#29416e"/>'
        body += "".join(
            f'<path transform="translate({1.1 + col * 2.06 + (row % 2) * 1.03:.2f} {1.1 + row * 1.34:.2f})" d="M0-.65.15-.2.62-.2.24.08.38.53 0 .25-.38.53-.24.08-.62-.2-.15-.2Z" fill="#fff"/>'
            for row in range(9)
            for col in range(6 if row % 2 == 0 else 5)
        )
    elif code == "tr":
        body = '<rect width="24" height="24" fill="#d92d3b"/><circle cx="10" cy="12" r="6" fill="#fff"/><circle cx="12" cy="11" r="4.8" fill="#d92d3b"/><path d="m17 8 1 3 3 .2-2.4 1.8.8 3-2.4-1.8-2.5 1.8.9-3-2.5-1.8 3-.2z" fill="#fff"/>'
    elif code == "zh":
        body = '<rect width="24" height="24" fill="#d82d32"/><path d="m7 4 1.2 3.4 3.6.1-2.9 2.2 1 3.4L7 11l-2.9 2.1 1-3.4L2.2 7.5l3.6-.1z" fill="#ffe05c"/><g fill="#ffe05c"><circle cx="14" cy="4.5" r=".9"/><circle cx="16" cy="7" r=".9"/><circle cx="16" cy="10" r=".9"/><circle cx="14" cy="12" r=".9"/></g>'
    elif code == "tc":
        body = '<rect width="24" height="24" fill="#d82d32"/><path d="M0 0h14v13H0z" fill="#24509b"/><g stroke="#fff" stroke-width=".8"><path d="M7 2v9M2.5 6.5h9M3.8 3.3l6.4 6.4M3.8 9.7l6.4-6.4"/></g><circle cx="7" cy="6.5" r="2.7" fill="#fff"/>'
    else:  # Korean Taegeuk and four trigrams, kept legible at icon size.
        body = '<rect width="24" height="24" fill="#fff"/><circle cx="12" cy="12" r="5.2" fill="#ce354a"/><path d="M6.8 12a5.2 5.2 0 0 0 10.4 0c-2.6-4-2.6 4-5.2 0s-5.2 0-5.2 0" fill="#22599c"/><g stroke="#222" stroke-width="1"><path d="m4 7 3-3m-2 5 4-4m8-1 3 3m-5-1 3 3M4 17l3 3m-2-5 4 4m8 1 3-3m-5 1 3-3"/></g>'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs><clipPath id="c"><circle cx="12" cy="12" r="11"/></clipPath></defs><g clip-path="url(#c)">'
        + body
        + '</g><circle cx="12" cy="12" r="11" fill="none" stroke="#8d9cab" stroke-width=".6"/></svg>'
    )


@lru_cache(maxsize=52)
def flag_pixmap(code, size):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Qt's SVG Tiny renderer does not consistently implement SVG clipPath.
    # Clip in the painter so every flag is a circle on every supported Qt build.
    clip = QPainterPath()
    clip.addEllipse(size / 12, size / 12, size * 11 / 6, size * 11 / 6)
    painter.setClipPath(clip)
    QSvgRenderer(QByteArray(flag_svg(code).encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap


def badge_size(metrics, *, compact=False):
    if compact:
        return metrics.height()
    return max(15, min(23, metrics.height() + 1))


def entry_width(text, metrics, *, compact=False):
    return metrics.horizontalAdvance(text) + badge_size(metrics, compact=compact) + 9


def draw_entry(painter, rect, text, code, metrics, color, *, compact=False):
    size = badge_size(metrics, compact=compact)
    painter.drawPixmap(
        rect.left(), rect.center().y() - size // 2, flag_pixmap(code, size)
    )
    shown = metrics.elidedText(
        text, Qt.TextElideMode.ElideRight, max(0, rect.width() - size - 9)
    )
    painter.setPen(color)
    painter.drawText(
        rect.adjusted(size + 6, 0, 0, 0),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        shown,
    )


class LanguageDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        style = QStyleOptionViewItem(option)
        self.initStyleOption(style, index)
        text = style.text
        style.text = ""
        style.widget.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, style, painter, style.widget
        )
        painter.save()
        painter.setFont(style.font)
        draw_entry(
            painter,
            style.rect.adjusted(8, 0, -8, 0),
            text,
            index.data(Qt.ItemDataRole.UserRole),
            style.fontMetrics,
            QColor("#e1edfa"),
        )
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        style = QStyleOptionViewItem(option)
        self.initStyleOption(style, index)
        return QSize(
            entry_width(style.text, style.fontMetrics) + 16,
            max(28, size.height()),
        )


class LanguageCombo(Combo):
    def __init__(self, parent=None):
        self._fitting = False
        super().__init__(parent)
        self.setObjectName("footerLanguagePicker")
        self.setAccessibleName("Language")
        self.setItemDelegate(LanguageDelegate(self))
        for code, name in NAMES.items():
            self.addItem(name, code)
        self.fit_contents()

    def fit_contents(self):
        if self._fitting or not self.count():
            return
        self._fitting = True
        try:
            self.ensurePolished()
            option = QStyleOptionComboBox()
            self.initStyleOption(option)
            # Measure the actual styled frame, padding and arrow, independently
            # of the current selection or the widget's previous width.
            option.rect.setWidth(1000)
            edit = self.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                self,
            )
            chrome = option.rect.width() - edit.width() + 3
            widest = max(
                entry_width(self.itemText(i), option.fontMetrics, compact=True)
                for i in range(self.count())
            )
            self.setFixedWidth(widest + chrome)
        finally:
            self._fitting = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.fit_contents()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_contents()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text = option.currentText
        option.currentText = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        draw_entry(
            painter,
            rect.adjusted(2, 0, -1, 0),
            text,
            self.currentData(),
            option.fontMetrics,
            QColor("#dce8f5"),
            compact=True,
        )


class LanguagePicker(QWidget):
    selected = Signal(str)

    def __init__(self, code, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = LanguageCombo()
        self.combo.activated.connect(
            lambda _: self.selected.emit(self.combo.currentData())
        )
        layout.addWidget(self.combo)
        self.set_language(code)
        self.set_ui_scale(1)

    def set_language(self, code):
        self.combo.setCurrentIndex(self.combo.findData(code))

    def set_ui_scale(self, scale, height=None):
        # Match the adjacent links' line height instead of making the whole
        # footer row as tall as a regular form control. The popup stays roomy.
        height = height if height is not None else self.fontMetrics().height()
        self.combo.setFixedHeight(height)
        self.setFixedHeight(height)
        self.combo.fit_contents()

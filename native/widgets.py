# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Reusable native controls for the compact companion layout."""

import html
import math
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from grading import GRADE_COLORS, grade_stat, grading_rank
from appearance import scaled_pixels


def label(text="", name="", wrap=False):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def rich(text="", name="", wrap=False):
    widget = RichLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


class RichLabel(QLabel):
    """Scale explicit HTML sizes without recalculating or replacing card data."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._source = text
        self._scale = 1
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(text)

    def setText(self, text):
        self._source = text
        self._render()

    def _render(self):
        text = scaled_pixels(self._source, self._scale)
        if text != self.text():
            super().setText(text)

    def clear(self):
        self.setText("")

    def set_ui_scale(self, scale):
        if scale != self._scale:
            self._scale = scale
            self._render()


class Hyperlink(RichLabel):
    """A local UI link with mouse and keyboard feedback."""

    def __init__(self, caption, url, parent=None):
        super().__init__(parent=parent)
        self.caption, self.url = caption, url
        self._hovered = False
        self.setObjectName("footerLink")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.refresh_link()

    def _render(self):
        super()._render()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        # QLabel's rich-text hit testing can restore the arrow cursor.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def refresh_link(self):
        highlighted = self._hovered or self.hasFocus()
        color = "#bce8ff" if highlighted else "#7ecbff"
        decoration = "underline" if highlighted else "none"
        self.setText(
            f'<a style="color:{color};text-decoration:{decoration}" '
            f'href="{html.escape(self.url, quote=True)}">{html.escape(self.caption)}</a>'
        )

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        self.refresh_link()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hovered = False
        self.refresh_link()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.refresh_link()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.refresh_link()


class LockButton(QToolButton):
    """One planner lock with distinct manual and automatically retained states."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statLock")
        self.setCheckable(True)
        self.icons = {}
        for name in ("open", "closed", "retained"):
            path = str(Path(__file__).resolve().parent / "data" / f"lock_{name}.svg")
            icon = QIcon(path)
            if name == "retained":
                icon.addFile(path, mode=QIcon.Mode.Disabled)
            self.icons[name] = icon
        self.set_ui_scale(1)

    def set_ui_scale(self, scale):
        self.setFixedSize(round(24 * scale), round(30 * scale))
        self.setIconSize(QSize(round(18 * scale), round(18 * scale)))

    def show_lock(self, name, locked=False, retained=False, available=True):
        self.setChecked(locked or retained)
        self.setEnabled(available and not retained)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self.isEnabled()
            else Qt.CursorShape.ArrowCursor
        )
        if self.property("retained") != retained:
            self.setProperty("retained", retained)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setIcon(
            self.icons["retained" if retained else "closed" if locked else "open"]
        )
        message = (
            f"{name}: splice retained automatically. One extra manual lock is available."
            if retained
            else "Select a stat to lock."
            if not available
            else f"Unlock {name}"
            if locked
            else f"Lock {name}"
        )
        self.setAccessibleName(message)
        self.setToolTip(message)


def box(vertical=True, margin=0, spacing=6):
    widget = QWidget()
    layout = QVBoxLayout(widget) if vertical else QHBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return widget, layout


def options(combo, rows, selected):
    rows = list(rows)
    before = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
    combo.blockSignals(True)
    if before != rows:
        combo.clear()
        for text, value in rows:
            combo.addItem(text, value)
    index = combo.findData(selected)
    combo.setCurrentIndex(max(0, index))
    combo.blockSignals(False)


class Combo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.setMinimumContentsLength(1)
        self.setMaxVisibleItems(18)

    def wheelEvent(self, event):
        # Scrolling a settings panel must not silently change a selected stat.
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class StatDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if brush:
            for role in (QPalette.ColorRole.Text, QPalette.ColorRole.HighlightedText):
                option.palette.setColor(QPalette.ColorGroup.All, role, brush.color())


class StatCombo(Combo):
    """Stat choices retain eligibility colors even when disabled or highlighted."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statSelector")
        self.setItemDelegate(StatDelegate(self))

    def set_eligibility(self, index, excluded, selectable, vintage, uncertain=False):
        item = self.model().item(index)
        item.setEnabled(selectable)
        color = "#ff757b" if excluded else "#dfb96b" if uncertain else None
        item.setData(
            QBrush(QColor(color)) if color else None, Qt.ItemDataRole.ForegroundRole
        )
        item.setData(
            "Vintage stat, already-existing line only"
            if vintage
            else "Not rollable on this weapon"
            if excluded
            else "Eligibility under research"
            if uncertain
            else "",
            Qt.ItemDataRole.AccessibleDescriptionRole,
        )


class Section(QFrame):
    toggled = Signal(bool)

    def __init__(self, title, opened=True, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.opened = opened
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header, row = box(False, spacing=0)
        header.setObjectName("sectionHeader")
        self.title = QPushButton(title)
        self.title.setObjectName("sectionTitle")
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.title.clicked.connect(self.toggle)
        self.summary = label("", "sectionSummary")
        self.arrow = QToolButton()
        self.arrow.setObjectName("sectionArrow")
        self.arrow.clicked.connect(self.toggle)
        row.addWidget(self.title, 1)
        row.addWidget(self.summary)
        row.addWidget(self.arrow)
        layout.addWidget(header)
        self.body, self.content = box(margin=9, spacing=6)
        self.body.setObjectName("stageBody")
        self.content.setContentsMargins(8, 0, 8, 7)
        self.content.setSpacing(3)
        layout.addWidget(self.body)
        self.refresh()

    def set_ui_scale(self, scale):
        self.content.setContentsMargins(
            round(8 * scale), 0, round(8 * scale), round(7 * scale)
        )
        self.content.setSpacing(max(3, round(3 * scale)))

    def refresh(self):
        self.body.setVisible(self.opened)
        self.summary.setVisible(not self.opened and bool(self.summary.text()))
        self.arrow.setArrowType(
            Qt.ArrowType.UpArrow if self.opened else Qt.ArrowType.DownArrow
        )
        self.arrow.setAccessibleName(
            "Collapse section" if self.opened else "Expand section"
        )

    def set_summary(self, text):
        self.summary.setText(text)
        self.refresh()

    def toggle(self):
        self.opened = not self.opened
        self.refresh()
        self.toggled.emit(self.opened)


def number(value, decimals=1):
    if value is None or not math.isfinite(value):
        return "∞"
    return (
        f"{value:,.{decimals}f}".rstrip("0").rstrip(".")
        if decimals
        else f"{value:,.0f}"
    )


def interval(value, decimals=1):
    if not value:
        return "Unavailable"
    low, high = number(value["min"], decimals), number(value["max"], decimals)
    return low if low == high else f"{low} to {high}"


def odds_text(probability):
    if not probability or probability["max"] <= 0:
        return "Not available"
    return "1 / " + interval(
        {
            "min": 1 / probability["max"],
            "max": 1 / probability["min"] if probability["min"] else math.inf,
        }
    )


class RollCard(QFrame):
    def __init__(self, title, new=False, parent=None):
        super().__init__(parent)
        self.setObjectName("newCard" if new else "currentCard")
        self.caption = title
        self._signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(4)
        header, header_row = box(False, spacing=3)
        self.meta = label(title, "cardMeta")
        self.stale = label("LAST READ", "stale")
        self.stale.hide()
        header_row.addWidget(self.meta, 1)
        header_row.addWidget(self.stale)
        layout.addWidget(header)
        self.stack = QStackedWidget()
        empty = label(
            "Waiting for a new roll to compare."
            if new
            else "Waiting for a clear Riven card.",
            "muted",
            True,
        )
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(empty)
        details, content = box(spacing=2)
        self.name = label("", "cardName")
        self.title = label("", "cardTitle")
        content.addWidget(self.name)
        content.addWidget(self.title)
        self.rows = []
        for _ in range(4):
            row, h = box(False, spacing=4)
            text = rich(wrap=True)
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text.setMinimumWidth(0)
            grade = rich()
            grade.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            h.addWidget(text, 1)
            h.addWidget(grade)
            content.addWidget(row, 1)
            self.rows.append((row, text, grade))
        self.stack.addWidget(details)
        layout.addWidget(self.stack, 1)
        self.setMinimumHeight(164)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_ui_scale(self, scale):
        self.layout().setContentsMargins(
            round(10 * scale), round(9 * scale), round(10 * scale), round(9 * scale)
        )
        self.layout().setSpacing(round(4 * scale))
        self.setMinimumHeight(round(164 * scale))

    def show_card(self, card, catalog, preferences):
        variant = catalog.variant(card, preferences["gradeVariants"]) if card else None
        rank = grading_rank(card, preferences) if card else None
        signature = repr(
            (
                card
                and {
                    k: v
                    for k, v in card.items()
                    if k in ("title", "weapon", "stats", "format")
                },
                variant,
                rank,
                preferences["rankMode"],
            )
        )
        self.stale.setVisible(bool(card and card.get("displayStale")))
        if signature == self._signature:
            return
        self._signature = signature
        if not card:
            self.meta.setText(self.caption)
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        rank_text = "RANK ?" if rank is None else f"RANK {rank}/8"
        if preferences["rankMode"] == "manual":
            rank_text += " MANUAL"
        self.meta.setText(f"{self.caption} · {card['format']} · {rank_text}")
        self.name.setText(variant["name"] if variant else card["weapon"])
        self.name.setToolTip(self.name.text())
        self.title.setText(card.get("title", ""))
        for index, (row, text, grade_label) in enumerate(self.rows):
            row.setVisible(index < len(card["stats"]))
            if index >= len(card["stats"]):
                continue
            stat = card["stats"][index]
            trait = catalog.trait(variant, stat["id"])
            name = trait["name"] if trait else stat.get("name", stat["id"])
            value = (
                f"{stat['value']:g}×"
                if stat["unit"] == "x"
                else f"{stat['value']:+g}{stat['unit']}"
            )
            color = "#c0a0e7" if stat["polarity"] == "positive" else "#ff9ca4"
            text.setText(
                f'<span style="color:{color}"><b>{html.escape(value)}</b> {html.escape(name)}</span>'
            )
            result = (
                grade_stat(
                    stat,
                    trait,
                    variant["disposition"],
                    card["format"],
                    catalog.range_model,
                    rank,
                )
                if variant
                else {"unknown": True, "message": "Variant unreadable: see Settings"}
            )
            if result.get("grade"):
                grade = result["grade"]
                grade_label.setText(
                    f'<b style="font-size:19px;color:{GRADE_COLORS[grade]}">{grade}</b> '
                    f'<span style="font-size:10px;color:#91a6bd">{result["quality"]:+.3f}%</span>'
                )
                tip = (
                    " ".join(
                        filter(
                            None,
                            [result.get("roundingNote"), result.get("baselineNote")],
                        )
                    )
                    or "Relative to the mean"
                )
            else:
                grade_label.setText(
                    '<span style="color:#ffbd69;font-weight:600">Check</span>'
                )
                tip = result["message"]
            grade_label.setToolTip(tip)
            text.setToolTip(f"{value} {name}")

    def set_warning(self, active, message=""):
        if self.property("warning") != active:
            self.setProperty("warning", active)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setToolTip(message if active else "")

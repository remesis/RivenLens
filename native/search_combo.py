# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Search-first weapon picker with predictable keyboard selection."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCompleter,
    QLineEdit,
    QStyle,
    QStyleOptionFrame,
    QStylePainter,
)

from widgets import Combo
from ui_text import language, translate


class SearchEdit(QLineEdit):
    """Keep the full selected value without exposing the editor's scroll offset."""

    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def display_rect(self, option):
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_LineEditContents, option, self
        )
        margins = self.textMargins()
        return rect.adjusted(
            margins.left() + 2, margins.top(), -margins.right() - 2, -margins.bottom()
        )

    def elided_label(self, option):
        return option.fontMetrics.elidedText(
            self.text(),
            Qt.TextElideMode.ElideRight,
            max(0, self.display_rect(option).width()),
        )

    def paintEvent(self, event):
        if self.combo._searching:
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        painter.drawPrimitive(QStyle.PrimitiveElement.PE_PanelLineEdit, option)
        painter.drawItemText(
            self.display_rect(option),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            option.palette,
            self.isEnabled(),
            self.elided_label(option),
            QPalette.ColorRole.Text,
        )


class SearchCombo(Combo):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._searching = False
        self.setEditable(True)
        self.setLineEdit(SearchEdit(self))
        self.setInsertPolicy(Combo.InsertPolicy.NoInsert)
        self.setCompleter(None)
        self.search = QCompleter(self.model(), self)
        self.search.setWidget(self.lineEdit())
        self.search.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.search.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.setMaxVisibleItems(18)
        self.search.activated[str].connect(self.select_match)
        self.lineEdit().setPlaceholderText("Search weapons...")
        language.changed.connect(self.translate_prompt)
        self.translate_prompt()
        self.lineEdit().textEdited.connect(self.filter_matches)
        self.lineEdit().editingFinished.connect(self.finish_search)
        self.lineEdit().installEventFilter(self)
        self.search.popup().installEventFilter(self)

    def translate_prompt(self):
        self.lineEdit().setPlaceholderText(translate("Search weapons..."))

    def showPopup(self):
        self._searching = True
        self.lineEdit().clear()
        self.lineEdit().setFocus()
        self.filter_matches("")

    def filter_matches(self, text):
        self._searching = True
        self.search.setCompletionPrefix(text)
        self.search.complete()
        first = self.search.completionModel().index(0, 0)
        self.search.popup().setCurrentIndex(first)

    def select_match(self, text):
        index = self.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0:
            return
        self._searching = False
        self.search.popup().hide()
        self.setCurrentIndex(index)
        self.setEditText(self.itemText(index))
        self.lineEdit().setCursorPosition(0)
        self.lineEdit().update()
        self.activated.emit(index)

    def finish_search(self):
        if not self._searching or self.search.popup().isVisible():
            return
        if self.findText(self.currentText(), Qt.MatchFlag.MatchFixedString) >= 0:
            self.select_match(self.currentText())
        else:
            self._searching = False
            self.setEditText(self.itemText(self.currentIndex()))
            self.lineEdit().setCursorPosition(0)
            self.lineEdit().update()

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            if not self._searching:
                self.showPopup()
        if event.type() == QEvent.Type.KeyPress and self._searching:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                index = self.search.popup().currentIndex()
                if index.isValid():
                    self.select_match(index.data())
                return True
            if event.key() == Qt.Key.Key_Escape:
                self._searching = False
                self.search.popup().hide()
                self.setEditText(self.itemText(self.currentIndex()))
                self.lineEdit().setCursorPosition(0)
                self.lineEdit().update()
                return True
        return super().eventFilter(watched, event)

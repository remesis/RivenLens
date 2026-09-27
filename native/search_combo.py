# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Search-first weapon picker with predictable keyboard selection."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QCompleter

from widgets import Combo
from ui_text import language, translate


class SearchCombo(Combo):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(Combo.InsertPolicy.NoInsert)
        self.setCompleter(None)
        self._searching = False
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
        self.activated.emit(index)

    def finish_search(self):
        if not self._searching or self.search.popup().isVisible():
            return
        if self.findText(self.currentText(), Qt.MatchFlag.MatchFixedString) >= 0:
            self.select_match(self.currentText())
        else:
            self._searching = False
            self.setEditText(self.itemText(self.currentIndex()))

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
                return True
        return super().eventFilter(watched, event)

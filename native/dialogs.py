# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Owned modal windows that stay above the companion while open."""

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog


class ModalDialog(QDialog):
    def __init__(self, parent):
        # Modality blocks input but does not reliably outrank a topmost owner.
        # Set this before opening, not during exec(), which could hide the dialog.
        super().__init__(
            parent.window(), Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._front_timer = QTimer(self)
        self._front_timer.setSingleShot(True)
        self._front_timer.timeout.connect(self.bring_to_front)

    def showEvent(self, event):
        super().showEvent(event)
        self.parentWidget().installEventFilter(self)
        self.bring_to_front()
        self._front_timer.start(0)

    def hideEvent(self, event):
        self._front_timer.stop()
        self.parentWidget().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Close:
            self.reject()
        elif watched is self.parentWidget() and event.type() in (
            QEvent.Type.Show,
            QEvent.Type.WindowActivate,
        ):
            # Owner flag changes and tray restores can rebuild/reorder its window.
            self._front_timer.start(0)
        return super().eventFilter(watched, event)

    def bring_to_front(self):
        if not self.isVisible():
            return
        active = QApplication.activeModalWidget()
        if active is not None and active is not self:
            # Leave nested dialogs, including the audio file picker, in control.
            return
        handle = self.windowHandle()
        owner = self.parentWidget().windowHandle()
        if handle is not None and owner is not None:
            if handle.transientParent() is not owner:
                handle.setTransientParent(owner)
        self.raise_()
        self.activateWindow()

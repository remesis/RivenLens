# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Consent, progress and verified completion for on-device OCR setup."""

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout

from appearance import theme_for
from dialogs import ModalDialog
from language_picker import NAMES
from ocr_engine import LocalOCR, available_languages, backend_for, recognizer_tag
from ocr_install import CONSENT_CANCELLED, RESTART_REQUIRED, OCRInstallSession
from ui_text import QPushButton
from widgets import label


def language_installed(code):
    if backend_for(code) == "rapid":
        from ocr_models import ready

        return ready(code)
    return bool(recognizer_tag(code, available_languages()))


class OCRSetupDialog(ModalDialog):
    def __init__(self, owner, setup):
        super().__init__(owner)
        self.rapid = setup.rapid
        self.setWindowTitle("OCR language setup")
        self.setStyleSheet(theme_for(1.08))
        self.setMinimumWidth(360)
        self.resize(460, 270)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self.heading = label(NAMES[setup.code], "heading")
        layout.addWidget(self.heading)
        self.message = label(wrap=True)
        layout.addWidget(self.message)
        self.details = label("", "muted", True)
        layout.addWidget(self.details)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        layout.addStretch()
        self.manual = QPushButton("Open Windows language settings")
        self.manual.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("ms-settings:regionlanguage"))
        )
        layout.addWidget(self.manual)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.close_button = QPushButton("No")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.reject)
        self.install_button = QPushButton("Install OCR language")
        if self.rapid:
            self.install_button.setText("Download local OCR files")
        self.install_button.setAutoDefault(False)
        self.install_button.clicked.connect(setup.install)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    def show_state(self, state, message, details="", *, code=None):
        if code is not None:
            self.rapid = backend_for(code) == "rapid"
            self.heading.setText(NAMES[code])
            self.install_button.setText(
                "Download local OCR files" if self.rapid else "Install OCR language"
            )
        busy = state == "installing"
        self.message.setText(message)
        self.details.setText(details)
        self.details.setVisible(bool(details))
        self.progress.setVisible(busy)
        self.manual.setVisible(not self.rapid and state not in ("installing", "ready"))
        self.install_button.setVisible(state in ("missing", "failed", "cancelled"))
        self.close_button.setText("No" if state == "missing" else "Close")
        self.adjustSize()


class OCRSetup(QObject):
    busy_changed = Signal(bool)
    ready = Signal(str, bool)
    message_changed = Signal(str)

    def __init__(self, owner):
        super().__init__(owner)
        self.dialog = None
        self.session = None
        self.code = "en"
        self.resume = False
        self.closed = False
        self.state = "missing"
        self.message = self.details = ""
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.poll)

    @property
    def busy(self):
        return self.session is not None

    @property
    def rapid(self):
        return backend_for(self.code) == "rapid"

    def ensure(self, code, *, resume=False):
        """Called only for an explicit language selection or Start OCR click."""
        if self.closed:
            return False
        if self.busy:
            self.show_dialog()
            return False
        self.code, self.resume = code, resume
        try:
            if language_installed(code):
                return True
        except Exception as exc:
            self.present(
                "unavailable",
                "Could not check local OCR files. Restart RivenLens or retry setup."
                if self.rapid
                else "Could not check Windows OCR languages. Open Windows language settings or restart RivenLens.",
                str(exc),
            )
            return False
        if self.rapid:
            self.present(
                "missing",
                "RapidOCR needs local recognition files. Download them now?",
                "The CPU runtime and this language's models are downloaded once from PyPI and ModelScope, checked and saved outside the app. No administrator access or GPU is needed. Screen images are never sent. Updates reuse these files.",
            )
            return False
        self.present(
            "missing",
            "Windows OCR is missing for this language. Install it now?",
            "Windows will download Basic typing and OCR features. Administrator approval and an internet connection are required. Your display language and keyboard will not change.",
        )
        return False

    def show_dialog(self):
        if self.closed:
            return
        if self.dialog is None:
            self.dialog = OCRSetupDialog(self.parent(), self)
            self.dialog.finished.connect(self.dialog_finished)
            self.dialog.show_state(
                self.state, self.message, self.details, code=self.code
            )
            self.dialog.open()
        else:
            self.dialog.show_state(
                self.state, self.message, self.details, code=self.code
            )
            self.dialog.bring_to_front()

    def dialog_finished(self, _):
        self.dialog = None

    def present(self, state, message, details="", *, open_dialog=True):
        self.state, self.message, self.details = state, message, details
        self.message_changed.emit(message)
        if self.dialog is not None or open_dialog:
            self.show_dialog()

    def install(self):
        if (
            self.busy
            or self.closed
            or self.state not in ("missing", "failed", "cancelled")
        ):
            return
        try:
            # Windows may have installed it since the prompt was opened.
            if not self.rapid and language_installed(self.code):
                self.complete()
                return
            if self.rapid:
                from rapid_setup import RapidInstallSession

                session = RapidInstallSession(self.code)
            else:
                session = OCRInstallSession(self.code)
            session.start()
            self.session = session
        except Exception as exc:
            self.present("failed", "Could not start OCR setup.", str(exc))
            return
        self.busy_changed.emit(True)
        if self.rapid:
            self.present(
                "installing",
                "Preparing local OCR files…",
                "You can close this window while setup continues. Closing RivenLens requests cancellation; an in-progress network operation may take up to 30 seconds to finish.",
            )
            self.timer.start()
            return
        self.present(
            "installing",
            "Installing OCR language…",
            "Approve the Windows administrator prompt. Installation may take several minutes. You can close this window; Windows will finish in the background.",
        )
        self.timer.start()

    def poll(self):
        if self.session is None or self.closed:
            return
        try:
            result = self.session.poll()
        except OSError as exc:
            self.timer.stop()
            self.present(
                "unavailable",
                "Could not check local OCR setup. Restart RivenLens before retrying."
                if self.rapid
                else "Could not check Windows OCR setup. Check Windows language settings before retrying.",
                str(exc),
                open_dialog=False,
            )
            return
        if result is None:
            if self.rapid:
                self.present(
                    "installing", self.session.message, self.details, open_dialog=False
                )
            return
        error = getattr(self.session, "error", "")
        verified = getattr(self.session, "verified", False) is True
        self.finish()
        if result == 0:
            self.complete(verified=verified)
        elif self.rapid:
            self.present(
                "cancelled" if result == CONSENT_CANCELLED else "failed",
                error or "RapidOCR setup failed. OCR remains paused.",
                open_dialog=False,
            )
        elif result == RESTART_REQUIRED:
            self.present(
                "restart",
                "Windows needs a restart to finish OCR setup. Save your work and restart Windows when convenient. RivenLens will not restart Windows for you.",
                open_dialog=False,
            )
        elif result == CONSENT_CANCELLED:
            self.present(
                "cancelled",
                "Windows administrator approval was cancelled. OCR remains paused.",
                open_dialog=False,
            )
        else:
            self.present(
                "failed",
                "Windows could not install the OCR language. Check your connection and Windows Update permissions, or install it in Windows language settings.",
                f"Windows error: 0x{result & 0xFFFFFFFF:08X}",
                open_dialog=False,
            )

    def complete(self, *, verified=False):
        try:
            # Do not treat the helper's exit code alone as proof OCR is usable.
            if self.rapid:
                if not verified or not language_installed(self.code):
                    raise RuntimeError("The local OCR files could not be verified.")
            elif not verified:
                engine = LocalOCR(self.code)
                engine.close()
        except Exception as exc:
            self.present(
                "failed" if self.rapid else "restart",
                "The local OCR files are not ready yet. Retry setup; OCR remains paused."
                if self.rapid
                else "The OCR feature is not ready yet. Restart RivenLens; if it is still unavailable, check Windows language settings or restart Windows.",
                str(exc),
                open_dialog=False,
            )
            return
        self.present("ready", "OCR language is ready.", open_dialog=False)
        self.ready.emit(self.code, self.resume)

    def finish(self):
        self.timer.stop()
        self.session = None
        self.busy_changed.emit(False)

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        if self.dialog is not None:
            self.dialog.reject()
        if self.session is not None and self.rapid:
            self.session.shutdown()
        # Never terminate Windows servicing. An already-approved helper can
        # complete after the app exits; the next launch checks actual OCR state.

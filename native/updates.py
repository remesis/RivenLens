# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""One startup check and a user-approved download, install and restart flow."""

import json
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from appearance import theme_for
from releases import ReleaseConfig, newer_release
from update_network import GithubTransfer
from update_session import InstallSession
from widgets import label


class UpdateDialog(QDialog):
    def __init__(self, window, config, release):
        super().__init__(window)
        self.owner = window
        self.config = config
        self.release = release
        self.session = None
        self.installing = False
        self.poll = QTimer(self)
        self.poll.setInterval(200)
        self.poll.timeout.connect(self.install_progress)
        self.setWindowTitle("RivenLens update")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(
            theme_for(1.08)
            + "QLabel#heading { font-size: 20px; } QProgressBar { border: 1px solid #2b3f51; border-radius: 4px; text-align: center; background: #101822; } QProgressBar::chunk { background: #548eaa; }"
        )
        self.setMinimumWidth(340)
        self.resize(400, 240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        layout.addWidget(label("Update available", "heading"))
        self.message = label(
            f"RivenLens {release.version} is available. You have {config.version}.\n\nWould you like to download and install the latest version?",
            wrap=True,
        )
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        self.note = label(
            "RivenLens will close briefly, install the update and reopen. Your settings and custom sounds will stay in place.",
            "muted",
            True,
        )
        layout.addWidget(self.note)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        layout.addStretch()
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.no = QPushButton("No")
        self.yes = QPushButton("Yes, update")
        self.yes.setObjectName("primary")
        self.no.setDefault(True)
        self.yes.setAutoDefault(False)
        self.no.clicked.connect(self.reject)
        self.yes.clicked.connect(self.download)
        buttons.addWidget(self.no)
        buttons.addWidget(self.yes)
        layout.addLayout(buttons)
        self.transfer = GithubTransfer(self)
        self.transfer.progress.connect(self.show_progress)
        self.transfer.completed.connect(self.prepare_install)
        self.transfer.failed.connect(self.failed)

    def download(self):
        try:
            if self.session is not None:
                self.session.cancel()
            self.session = InstallSession(
                self.owner.preferences.directory, self.config, self.release
            )
        except OSError:
            self.failed(
                "Could not prepare the local update folder. Check free space and permissions."
            )
            return
        self.message.setText(f"Downloading RivenLens {self.release.version}…")
        self.yes.hide()
        self.no.setText("Cancel")
        self.progress.setRange(0, 0)
        self.progress.show()
        self.transfer.start(
            self.release.url,
            destination=self.session.archive,
            sha256=self.release.sha256,
            size=self.release.size,
        )

    def show_progress(self, received, total):
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(round(received / total * 100))

    def prepare_install(self, _):
        self.message.setText("Preparing the update…")
        self.progress.setRange(0, 0)
        try:
            self.session.start()
        except OSError:
            self.failed(
                "Could not start the update helper. Your current version is unchanged."
            )
            return
        self.poll.start()

    def install_progress(self):
        result = self.session.progress()
        self.message.setText(result["message"])
        if result["state"] == "ready":
            self.poll.stop()
            self.installing = True
            self.session.handed_off = True
            self.no.setEnabled(False)
            self.owner.close()
        elif result["state"] in ("failed", "recovery"):
            self.failed(result["message"])

    def failed(self, message):
        self.poll.stop()
        self.progress.hide()
        self.message.setText(message)
        self.yes.setText("Try again")
        self.yes.show()
        self.no.setText("Close")

    def done(self, result):
        self.transfer.cancel()
        self.poll.stop()
        if self.session is not None:
            self.session.cancel()
        super().done(result)


class StartupUpdates(QObject):
    def __init__(self, window, config=None):
        super().__init__(window)
        self.window = window
        self.config = config or ReleaseConfig.load()
        self.checked = False
        self.stopped = False
        self.dialog = None
        self.transfer = GithubTransfer(self)
        self.transfer.completed.connect(self.received)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.check)

    def schedule(self):
        if self.config.enabled and not self.checked and not self.stopped:
            self.timer.start(900)

    def check(self):
        if self.checked or self.stopped or not self.config.enabled:
            return
        self.checked = True
        self.transfer.start(self.config.api_url)

    def received(self, data):
        if self.stopped or getattr(self.window, "_closing", False):
            return
        try:
            release = newer_release(self.config, json.loads(data))
        except (ValueError, TypeError, UnicodeError):
            return
        if release is not None:
            self.dialog = UpdateDialog(self.window, self.config, release)
            self.dialog.finished.connect(self.clear_dialog)
            self.dialog.show()

    def clear_dialog(self, _):
        self.dialog = None

    def shutdown(self):
        self.stopped = True
        self.timer.stop()
        self.transfer.cancel()
        if self.dialog is not None:
            self.dialog.reject()

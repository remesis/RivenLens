# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""RivenLens native entry point. Capture remains paused until explicitly started."""

import argparse
import hashlib
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parent
sys.path.insert(0, str(NATIVE.parent / "app"))

from PySide6.QtCore import QLockFile, QStandardPaths, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from appearance import theme_for  # noqa: E402
from preferences import Preferences  # noqa: E402
from releases import ReleaseConfig  # noqa: E402
from updates import StartupUpdates  # noqa: E402
from update_session import update_result  # noqa: E402
from window import MainWindow  # noqa: E402


def main():
    arguments = argparse.ArgumentParser(add_help=False)
    arguments.add_argument("--update-result")
    options, _ = arguments.parse_known_args()
    app = QApplication(sys.argv)
    app.setOrganizationName("Arbitrations")
    app.setApplicationName("RivenLens Native")
    release = ReleaseConfig.load()
    app.setApplicationVersion(release.version)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#0b0f14"),
        (QPalette.ColorRole.Base, "#182432"),
        (QPalette.ColorRole.Text, "#e5edf7"),
        (QPalette.ColorRole.WindowText, "#e5edf7"),
        (QPalette.ColorRole.ButtonText, "#e5edf7"),
        (QPalette.ColorRole.Highlight, "#28516a"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(theme_for())
    directory = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
    )
    locks = directory / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(str(NATIVE.parent).casefold().encode()).hexdigest()[:24]
    instance = QLockFile(str(locks / (identity + ".lock")))
    instance.setStaleLockTime(0)
    if not instance.tryLock(0):
        QMessageBox.information(
            None, "RivenLens", "This copy of RivenLens is already open."
        )
        return 0
    try:
        window = MainWindow(Preferences(directory))
    except Exception as exc:
        QMessageBox.critical(None, "RivenLens could not start", str(exc))
        return 1
    window.show()
    window.updates = StartupUpdates(window, release)
    app.aboutToQuit.connect(window.updates.shutdown)
    result = update_result(directory, options.update_result)
    if result is None:
        window.updates.schedule()
    elif result:
        QTimer.singleShot(
            400, lambda: QMessageBox.warning(window, "RivenLens update", result)
        )
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

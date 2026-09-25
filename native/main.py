# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""RivenLens native entry point. Capture remains paused until explicitly started."""

import argparse
import sys
import traceback
from pathlib import Path

NATIVE = Path(__file__).resolve().parent
sys.path.insert(0, str(NATIVE.parent / "app"))

from PySide6.QtCore import QStandardPaths, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QIcon, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from appearance import theme_for  # noqa: E402
from desktop import ICON, set_taskbar_identity  # noqa: E402
from instance import InstallationLease  # noqa: E402
from preferences import Preferences  # noqa: E402
from releases import ReleaseConfig  # noqa: E402
from updates import StartupUpdates  # noqa: E402
from update_session import update_result  # noqa: E402
from window import MainWindow  # noqa: E402


def main():
    arguments = argparse.ArgumentParser(add_help=False)
    arguments.add_argument("--update-result")
    options, _ = arguments.parse_known_args()
    set_taskbar_identity()
    app = QApplication(sys.argv)
    app.setOrganizationName("Arbitrations")
    app.setApplicationName("RivenLens Native")
    app.setApplicationDisplayName("RivenLens")
    app.setWindowIcon(QIcon(str(ICON)))
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
    instance = InstallationLease(NATIVE.parent, directory / "locks")
    if not instance.acquire():
        QMessageBox.information(
            None,
            "RivenLens",
            "This copy of RivenLens is already open or being updated.",
        )
        return 0
    try:
        if (NATIVE / "update-pending.json").exists():
            QMessageBox.warning(
                None,
                "RivenLens update",
                "An update needs attention. Reopen RivenLens with its launcher to finish or recover it.",
            )
            return 0
        return run_window(app, directory, release, options)
    finally:
        instance.close()


def run_window(app, directory, release, options):
    try:
        window = MainWindow(Preferences(directory))
    except Exception as exc:
        traceback.print_exc()
        QMessageBox.critical(None, "RivenLens could not start", str(exc))
        return 0  # Already reported; avoid a second supervisor dialog.
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

# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Optional feature setup without elevation, downloads or live user settings."""

import base64
import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "native"), str(ROOT / "app")]

from ocr_install import (  # noqa: E402
    CAPABILITY_TAGS,
    OCRInstallSession,
    installation_script,
    launch_script,
)


class InstallerTests(unittest.TestCase):
    def test_only_fixed_basic_and_ocr_capabilities_are_elevated(self):
        for code, tag in CAPABILITY_TAGS.items():
            script = installation_script(code)
            self.assertEqual(
                re.findall(r"Language\.[A-Za-z]+~~~[^']+", script),
                [f"Language.{part}~~~{tag}~0.0.1.0" for part in ("Basic", "OCR")],
            )
            wrapper = launch_script(code)
            payload = re.search(r"'-EncodedCommand', '([^']+)'", wrapper)[1]
            self.assertEqual(base64.b64decode(payload).decode("utf-16-le"), script)
            self.assertIn("-Verb RunAs -WindowStyle Hidden", wrapper)
            self.assertIn("$verified.State -ne 'Installed'", script)
            self.assertNotIn("Restart-Computer", script)
            self.assertNotIn("Set-Win", script)
            self.assertNotIn("ExecutionPolicy", wrapper)
        for invalid in ("uk", "th", "fr'; exit 0; #", "../../payload", None):
            with self.assertRaises(KeyError):
                OCRInstallSession(invalid)

    @unittest.skipUnless(sys.platform == "win32", "Windows process flags")
    def test_launch_is_hidden_nonblocking_and_never_uses_shell_lookup(self):
        executable = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        process = Mock()
        process.poll.side_effect = [None, 1223]
        with (
            patch("ocr_install.system_powershell", return_value=executable),
            patch("ocr_install.subprocess.Popen", return_value=process) as start,
        ):
            session = OCRInstallSession("fr")
            self.assertIsNone(session.poll())
            start.assert_not_called()
            session.start()
            args, kwargs = start.call_args
            self.assertEqual(args[0][0], str(executable))
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(
                base64.b64decode(args[0][-1]).decode("utf-16-le"), launch_script("fr")
            )
            self.assertIsNone(session.poll())
            self.assertEqual(session.poll(), 1223)
            with self.assertRaises(RuntimeError):
                session.start()
            process.wait.assert_not_called()


@unittest.skipUnless(
    sys.platform == "win32" and importlib.util.find_spec("PySide6"),
    "Install Windows app dependencies for setup UI tests",
)
class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtWidgets import QWidget
        from ocr_setup import OCRSetup

        self.owner = QWidget()
        self.setup = OCRSetup(self.owner)
        self.installed = self.enterContext(
            patch("ocr_setup.language_installed", return_value=False)
        )
        self.engine = self.enterContext(patch("ocr_setup.LocalOCR"))
        self.session_type = self.enterContext(patch("ocr_setup.OCRInstallSession"))
        self.session = self.session_type.return_value
        self.session.poll.return_value = None
        self.ready = Mock()
        self.setup.ready.connect(self.ready)

    def tearDown(self):
        self.setup.shutdown()
        self.owner.deleteLater()
        self.app.processEvents()

    def begin(self, resume=True):
        self.assertFalse(self.setup.ensure("fr", resume=resume))
        self.setup.dialog.install_button.click()
        self.assertTrue(self.setup.busy)
        self.session.start.assert_called_once()

    def test_missing_feature_requires_explicit_install_consent(self):
        self.assertFalse(self.setup.ensure("fr", resume=True))
        self.assertEqual(self.setup.state, "missing")
        self.session_type.assert_not_called()
        self.setup.dialog.close_button.click()
        self.assertIsNone(self.setup.dialog)
        for _ in range(10):
            self.setup.poll()
        self.session_type.assert_not_called()
        self.ready.assert_not_called()

    def test_installed_language_needs_no_prompt(self):
        self.installed.return_value = True
        self.assertTrue(self.setup.ensure("fr"))
        self.assertIsNone(self.setup.dialog)
        self.session_type.assert_not_called()

    def test_failed_probe_does_not_offer_elevation(self):
        self.installed.side_effect = OSError("Recognizer query failed")
        self.assertFalse(self.setup.ensure("fr"))
        self.assertEqual(self.setup.state, "unavailable")
        self.assertTrue(self.setup.dialog.install_button.isHidden())
        self.setup.install()
        self.session_type.assert_not_called()

    def test_success_requires_working_recognizer_before_resume(self):
        self.begin()
        self.setup.install()
        self.session.start.assert_called_once()
        self.setup.poll()
        self.ready.assert_not_called()
        self.session.poll.return_value = 0
        self.setup.poll()
        self.engine.assert_called_once_with("fr")
        self.ready.assert_called_once_with("fr", True)
        self.assertEqual(self.setup.state, "ready")
        self.assertFalse(self.setup.busy)

    def test_language_selection_while_paused_does_not_start_capture(self):
        self.begin(resume=False)
        self.session.poll.return_value = 0
        self.setup.poll()
        self.ready.assert_called_once_with("fr", False)

    def test_exit_zero_without_usable_recognizer_is_not_success(self):
        self.begin()
        self.engine.side_effect = RuntimeError("Not ready")
        self.session.poll.return_value = 0
        self.setup.poll()
        self.assertEqual(self.setup.state, "restart")
        self.ready.assert_not_called()

    def test_cancel_failure_and_restart_never_resume_or_retry_automatically(self):
        for code, state in (
            (1223, "cancelled"),
            (3010, "restart"),
            (-2146498529, "failed"),
        ):
            with self.subTest(code=code):
                self.setup.ensure("fr", resume=True)
                self.setup.install()
                self.session.poll.return_value = code
                self.setup.poll()
                self.assertEqual(self.setup.state, state)
                self.assertFalse(self.setup.busy)
                count = self.session.start.call_count
                for _ in range(10):
                    self.setup.poll()
                self.assertEqual(self.session.start.call_count, count)
                self.ready.assert_not_called()
                self.engine.assert_not_called()
                self.setup.dialog.reject()

    def test_dismissed_progress_does_not_reopen_on_completion(self):
        self.begin()
        self.setup.dialog.close_button.click()
        self.assertTrue(self.setup.busy)
        self.session.poll.return_value = 0
        self.setup.poll()
        self.assertIsNone(self.setup.dialog)
        self.ready.assert_called_once_with("fr", True)

    def test_shutdown_does_not_terminate_windows_servicing(self):
        self.begin()
        self.setup.shutdown()
        self.setup.poll()
        self.assertFalse(self.setup.timer.isActive())
        self.session.terminate.assert_not_called()
        self.session.kill.assert_not_called()
        self.session.poll.assert_not_called()
        self.assertFalse(self.setup.ensure("de"))

    def test_failed_process_launch_can_be_retried_explicitly(self):
        self.setup.ensure("fr")
        self.session.start.side_effect = OSError("Blocked")
        self.setup.install()
        self.assertFalse(self.setup.busy)
        self.assertEqual(self.setup.state, "failed")
        self.session.start.side_effect = None
        self.setup.install()
        self.assertTrue(self.setup.busy)

    def test_setup_dialog_is_owned_and_topmost(self):
        from PySide6.QtCore import Qt

        self.setup.ensure("fr")
        dialog = self.setup.dialog
        self.assertTrue(dialog.isModal())
        self.assertIs(dialog.parentWidget(), self.owner)
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_setup_does_not_keep_its_closed_owner_in_a_reference_cycle(self):
        import weakref
        from PySide6.QtWidgets import QWidget
        from ocr_setup import OCRSetup

        owner = QWidget()
        owner.ocr_setup = OCRSetup(owner)
        reference = weakref.ref(owner)
        owner.ocr_setup.shutdown()
        del owner
        self.assertIsNone(reference())

    def test_language_change_pauses_before_offer_and_preserves_resume_intent(self):
        from window import MainWindow
        from ui_text import language

        self.addCleanup(language.set, "en")
        owner = SimpleNamespace(
            running=True,
            state={"capture": {"language": "en"}},
            ocr_setup=self.setup,
            language_picker=Mock(),
            planner=Mock(),
            apply_capture=Mock(),
            persist=Mock(),
            schedule_balance=Mock(),
        )
        owner.apply_capture.side_effect = lambda: self.assertFalse(owner.running)
        MainWindow.set_language(owner, "fr")
        self.assertFalse(owner.running)
        self.assertEqual(owner.state["capture"]["language"], "fr")
        self.assertTrue(self.setup.resume)
        owner.apply_capture.assert_called_once()
        self.session_type.assert_not_called()

    def test_unknown_process_state_does_not_launch_another_installer(self):
        self.begin()
        self.session.poll.side_effect = OSError("Process status unavailable")
        self.setup.poll()
        self.assertEqual(self.setup.state, "unavailable")
        self.assertTrue(self.setup.busy)
        self.setup.install()
        self.session.start.assert_called_once()
        self.ready.assert_not_called()

    def test_main_window_waits_for_consent_before_starting(self):
        from window import MainWindow

        owner = SimpleNamespace(
            _closing=False,
            start=Mock(),
            running=False,
            state={"capture": {"language": "fr"}},
            ocr_setup=self.setup,
            apply_capture=Mock(),
        )
        owner.start.isEnabled.return_value = True
        MainWindow.toggle_capture(owner)
        self.assertFalse(owner.running)
        owner.apply_capture.assert_not_called()
        self.setup.dialog.reject()
        self.installed.return_value = True
        MainWindow.toggle_capture(owner)
        self.assertTrue(owner.running)
        owner.apply_capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()

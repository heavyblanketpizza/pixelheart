"""Regression coverage for Qt object lifetime across unittest boundaries."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from pixelheart.app import MainWindow
from tests.qt_support import QtTestCase


class QtCleanupTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_repeated_main_window_fixtures_return_to_widget_baseline(self):
        baseline = set(self.app.allWidgets())
        windows = []

        class WindowFixture(QtTestCase):
            def runTest(self):
                self.window = MainWindow(auto_download_icons=False)
                windows.append(self.window)

            def tearDown(self):
                self.window.dirty = False
                self.window.close()
                self.window.deleteLater()
                QApplication.processEvents()

        for _ in range(3):
            result = unittest.TestResult()
            WindowFixture().run(result)
            self.assertTrue(result.wasSuccessful(), result.errors)
            self.assertFalse(isValid(windows[-1]))
            self.assertEqual(set(self.app.allWidgets()), baseline)
            self.assertIs(QApplication.instance(), self.app)

    def test_registered_cleanups_complete_before_widgets_are_destroyed(self):
        baseline = set(self.app.allWidgets())
        observed = []
        widgets = []
        paths = []

        class CleanupFixture(QtTestCase):
            def setUp(self):
                self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                paths.append(self.root)
                self.widget = QWidget()
                widgets.append(self.widget)
                self.widget.destroyed.connect(lambda: observed.append(self.root.exists()))
                self.addCleanup(lambda: observed.append(isValid(self.widget)))
                self.addCleanup(self.widget.deleteLater)

            def runTest(self):
                pass

        result = unittest.TestResult()
        CleanupFixture().run(result)
        self.assertTrue(result.wasSuccessful(), result.errors)
        self.assertEqual(observed, [True, False])
        self.assertFalse(paths[0].exists())
        self.assertFalse(isValid(widgets[0]))
        self.assertEqual(set(self.app.allWidgets()), baseline)

    def test_failed_setup_still_deletes_registered_widgets_and_restores_mocks(self):
        baseline = set(self.app.allWidgets())
        widgets = []
        target = type("Target", (), {"value": "original"})

        class FailingFixture(QtTestCase):
            def setUp(self):
                self.enterContext(patch.object(target, "value", "patched"))
                widget = QWidget()
                widgets.append(widget)
                self.addCleanup(widget.deleteLater)
                raise RuntimeError("synthetic setup failure")

            def runTest(self):
                raise AssertionError("The test body must not run")

        result = unittest.TestResult()
        FailingFixture().run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("synthetic setup failure", result.errors[0][1])
        self.assertEqual(target.value, "original")
        self.assertFalse(isValid(widgets[0]))
        self.assertEqual(set(self.app.allWidgets()), baseline)

    def test_teardown_and_cleanup_failures_do_not_skip_deferred_deletion(self):
        baseline = set(self.app.allWidgets())
        widgets = []

        class FailingFixture(QtTestCase):
            def setUp(self):
                widget = QWidget()
                widgets.append(widget)
                self.addCleanup(widget.deleteLater)
                self.addCleanup(self.fail_cleanup)

            def fail_cleanup(self):
                raise RuntimeError("synthetic cleanup failure")

            def runTest(self):
                pass

            def tearDown(self):
                raise RuntimeError("synthetic teardown failure")

        result = unittest.TestResult()
        FailingFixture().run(result)
        self.assertEqual(len(result.errors), 2)
        self.assertIn("synthetic teardown failure", result.errors[0][1])
        self.assertIn("synthetic cleanup failure", result.errors[1][1])
        self.assertFalse(isValid(widgets[0]))
        self.assertEqual(set(self.app.allWidgets()), baseline)


if __name__ == "__main__":
    unittest.main()

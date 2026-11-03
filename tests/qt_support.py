"""Shared event processing and cleanup for Qt tests."""

import time
import unittest

from PySide6.QtCore import QCoreApplication, QEvent


class QtTestCase(unittest.TestCase):
    gui_timeout_seconds = 3

    def wait_until(self, predicate, message="Background GUI operation did not finish"):
        deadline = time.monotonic() + self.gui_timeout_seconds
        while True:
            QCoreApplication.processEvents()
            if predicate():
                return
            if time.monotonic() >= deadline:
                self.fail(message)
            time.sleep(0.005)  # Let Python workers run between GUI events.

    def doCleanups(self):
        try:
            return super().doCleanups()
        finally:
            # The game connection reads settings a test may have patched.
            from meetcute.game_connection import reset_game_connection
            reset_game_connection()
            # processEvents() alone does not deliver DeferredDelete events when
            # unittest, rather than Qt's event loop, drives the application.
            # Wait until worker shutdown, temporary files, mocks and registered
            # deleteLater callbacks have all completed before draining them.
            if QCoreApplication.instance() is not None:
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

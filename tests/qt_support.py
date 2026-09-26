"""Lifecycle support for tests sharing the process-wide QApplication."""

import unittest

from PySide6.QtCore import QCoreApplication, QEvent


class QtTestCase(unittest.TestCase):
    """Finish deferred QObject deletion after every test's own cleanup."""

    def doCleanups(self):
        try:
            return super().doCleanups()
        finally:
            # processEvents() alone does not deliver DeferredDelete events when
            # unittest, rather than Qt's event loop, drives the application.
            # Wait until worker shutdown, temporary files, mocks and registered
            # deleteLater callbacks have all completed before draining them.
            if QCoreApplication.instance() is not None:
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

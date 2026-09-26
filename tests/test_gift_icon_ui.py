"""Actual gift widgets keep local preview sprites separate from gift identity."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QPushButton

from pixelheart.gifts_page import GIFT_MIME, GiftsPage, ItemIconsDialog
from pixelheart.item_icons import ItemIconStore, export_filename
from tests.qt_support import QtTestCase


class GiftIconUITests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exports = self.root / "patch export"
        self.exports.mkdir()
        self.cache = self.root / "cache"
        self.store = ItemIconStore(self.cache)
        # Patch construction too, so tests never depend on the user's icon cache.
        with patch("pixelheart.gifts_page.ItemIconStore", return_value=self.store):
            self.page = GiftsPage()
        self.addCleanup(self.page.deleteLater)
        self.page.resize(1100, 760)
        self.page.show()
        self.app.processEvents()
        self.record = {"id": "Example.Mod_Coffee", "name": "Mod Coffee", "category": -7,
                       "category_name": "Cooking", "icon": {"texture": "Mods/Example/Objects", "index": 1}}

    def set_custom_catalog(self):
        self.page.load_catalog_snapshot({
            "format": "pixelheart-item-catalog", "version": 1, "label": "Test mod objects",
            "source": "content-patcher-export", "imported_at": "2026-09-19T00:00:00Z",
            "items": [self.record], "warnings": [],
        })

    def write_test_atlas(self):
        # Synthetic colored tiles validate cropping, never substitute for game art.
        image = QImage(32, 16, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        for x in range(16, 32):
            for y in range(16):
                image.setPixelColor(x, y, QColor("blue"))
        path = self.exports / export_filename(self.record["icon"]["texture"])
        self.assertTrue(image.save(str(path), "PNG"))
        return path

    @staticmethod
    def icon_color(item):
        return item.icon().pixmap(48, 48).toImage().pixelColor(24, 24).name()

    def dialog(self):
        dialog = ItemIconsDialog(self.store, [self.record], self.page)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_default_library_and_each_allocation_use_icon_mode_and_nonnull_icons(self):
        self.assertEqual(self.page.library.count(), 637)
        self.assertTrue(all(not self.page.library.item(row).icon().isNull()
                            for row in range(self.page.library.count())))
        for taste, item_id in zip(self.page.lists, ("395", "421", "66", "24")):
            self.assertTrue(self.page.assign_items([item_id], taste))
            target = self.page.lists[taste]
            self.assertEqual(target.viewMode(), QListWidget.ViewMode.IconMode)
            self.assertFalse(target.item(0).icon().isNull())
            self.assertEqual(target.item(0).data(Qt.ItemDataRole.UserRole), "(O)" + item_id)
        self.assertEqual(self.page.library.viewMode(), QListWidget.ViewMode.IconMode)
        self.assertFalse(self.cache.exists())

    def test_assign_button_and_texture_import_keep_ids_and_refresh_both_views(self):
        self.set_custom_catalog()
        self.write_test_atlas()
        self.page.library.item(0).setSelected(True)
        self.page.target.setCurrentIndex(self.page.target.findData("love"))
        assign = next(button for button in self.page.findChildren(QPushButton) if button.text() == "Assign")
        QTest.mouseClick(assign, Qt.MouseButton.LeftButton)
        self.assertEqual(self.page.dump()["love"], ["(O)Example.Mod_Coffee"])
        assignment_before = self.page.dump()
        changed = QSignalSpy(self.page.changed)

        def choose_and_close(dialog):
            with patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=str(self.exports)):
                dialog.choose_folder()
            self.assertIn("1 textures imported; 1 item sprites available", dialog.status.text())
            dialog.reject()
            return QDialog.DialogCode.Rejected

        with patch.object(ItemIconsDialog, "exec", choose_and_close):
            self.page.import_icons()
        self.assertEqual(self.icon_color(self.page.library.item(0)), "#0000ff")
        self.assertEqual(self.icon_color(self.page.lists["love"].item(0)), "#0000ff")
        self.assertEqual(self.page.dump(), assignment_before)
        self.assertEqual(changed.count(), 0)
        self.assertIn("Game sprites: 1 of 1", self.page.icon_status.text())
        self.assertNotIn(str(self.cache), json.dumps(self.page.catalog_snapshot()))

    def test_drag_uses_displayed_icon_and_qualified_id_payload(self):
        self.set_custom_catalog()
        self.write_test_atlas()
        self.store.import_folder(self.exports, [self.record])
        self.page.render()
        self.page.library.item(0).setSelected(True)
        captured = {}

        class Drag:
            def __init__(self, source):
                captured["source"] = source

            def setMimeData(self, mime):
                captured["mime"] = mime

            def setPixmap(self, pixmap):
                captured["pixmap"] = pixmap

            def setHotSpot(self, point):
                captured["hotspot"] = point

            def exec(self, *args):
                captured["actions"] = args

        with patch("pixelheart.gifts_page.QDrag", Drag):
            self.page.library.startDrag(Qt.DropAction.MoveAction)
        self.assertIs(captured["source"], self.page.library)
        self.assertEqual(json.loads(bytes(captured["mime"].data(GIFT_MIME))), ["(O)Example.Mod_Coffee"])
        center = captured["pixmap"].rect().center()
        self.assertEqual(captured["pixmap"].toImage().pixelColor(center).name(), "#0000ff")
        self.assertEqual(captured["pixmap"].size(), self.page.library.iconSize())
        self.assertEqual((captured["hotspot"].x(), captured["hotspot"].y()),
                         (self.page.library.iconSize().width() // 2, self.page.library.iconSize().height() // 2))

    def test_dialog_copy_commands_uses_exact_readonly_texture_commands(self):
        dialog = self.dialog()
        self.assertTrue(dialog.commands.isReadOnly())
        copy_button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Copy commands")
        QTest.mouseClick(copy_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.app.clipboard().text(), 'patch export "Mods/Example/Objects" image')
        self.assertIn("Commands copied", dialog.status.text())
        self.assertFalse(self.cache.exists())

    def test_dialog_reports_missing_unreadable_and_failed_folder_imports(self):
        dialog = self.dialog()
        with patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=str(self.exports)):
            dialog.choose_folder()
        self.assertIn("0 textures imported; 0 item sprites available", dialog.status.text())
        self.assertIn("1 textures were missing or unreadable", dialog.status.text())
        path = self.exports / export_filename(self.record["icon"]["texture"])
        path.write_text("not PNG")
        with patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=str(self.exports)):
            dialog.choose_folder()
        self.assertIn("must be a PNG", dialog.status.text())
        with patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=str(self.root / "missing")):
            dialog.choose_folder()
        self.assertIn("Could not load item icons", dialog.status.text())
        self.assertFalse(self.cache.exists())

    def test_cancel_does_not_import_or_write_and_legacy_metadata_is_explained(self):
        dialog = self.dialog()
        with patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=""), \
                patch.object(self.store, "import_folder") as import_folder:
            dialog.choose_folder()
        import_folder.assert_not_called()
        dialog.reject()
        self.assertFalse(self.cache.exists())
        legacy = ItemIconsDialog(self.store, [{"id": "395", "name": "Coffee"}], self.page)
        self.addCleanup(legacy.deleteLater)
        self.assertEqual(legacy.commands.toPlainText(), "")
        self.assertIn("no sprite references", legacy.status.text())


if __name__ == "__main__":
    unittest.main()

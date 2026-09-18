"""Download item previews without blocking the editor's GUI thread."""

from PySide6.QtCore import QThread, Signal

from pixelheart_core.wiki_items import download_item_icons


class WikiGiftDownload(QThread):
    item_ready = Signal(str)
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, item_ids, cache_root, parent=None):
        super().__init__(parent)
        self.item_ids = list(item_ids)
        self.cache_root = cache_root

    def run(self):
        count = 0

        def progress(item_id, path):
            nonlocal count
            count += 1
            if not self.isInterruptionRequested():
                if path is not None:
                    self.item_ready.emit(item_id)
                self.progress.emit(count, len(self.item_ids))

        try:
            result = download_item_icons(self.item_ids, self.cache_root,
                                         cancelled=self.isInterruptionRequested, progress=progress)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))

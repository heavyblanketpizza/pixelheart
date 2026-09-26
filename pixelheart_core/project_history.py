"""Bounded, in-memory undo history for an entire character project."""

from collections.abc import Hashable
from copy import deepcopy
from typing import Any


class ProjectHistory:
    """Keep project snapshots private and start a fresh history after saving.

    Documents are copied when received and returned. Stored snapshots are never
    changed in place, so nested metadata belongs to the snapshot that captured
    it. Nothing in this class is serialized into the project document.

    A non-None ``merge_key`` combines consecutive records into one undo step.
    Call ``close_group`` when navigation or focus changes end that editing
    gesture. Undo, redo, reset, and successful saves also end the group.
    """

    def __init__(self, document: dict[str, Any], *, max_entries: int = 50):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self._max_entries = max_entries
        self.reset(document)

    @property
    def current_document(self) -> dict[str, Any]:
        """Return an independent copy of the current project."""
        return deepcopy(self._current)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)

    @property
    def is_dirty(self) -> bool:
        """Compare with the saved project, even after older steps are evicted."""
        return self._current != self._saved

    def record(self, document: dict[str, Any], *, merge_key: Hashable | None = None) -> bool:
        """Capture an edit, returning whether the document changed.

        An unchanged document preserves redo and the current edit group. A new
        edit after undo discards redo. Returning to a group's starting document
        removes that now-empty undo step.
        """
        snapshot = deepcopy(document)
        if snapshot == self._current:
            return False

        merge = merge_key is not None and merge_key == self._group_key and bool(self._undo)
        if merge and snapshot == self._undo[-1]:
            self._undo.pop()
            self._group_key = None
        elif merge:
            self._group_key = merge_key
        else:
            self._undo.append(self._current)
            del self._undo[:-self._max_entries]
            self._group_key = merge_key

        self._current = snapshot
        self._redo.clear()
        return True

    def close_group(self) -> None:
        """Make the next changed document start a separate undo step."""
        self._group_key = None

    def undo(self) -> dict[str, Any] | None:
        """Restore the previous project, or return None when no step remains."""
        self.close_group()
        if not self._undo:
            return None
        self._redo.append(self._current)
        self._current = self._undo.pop()
        return self.current_document

    def redo(self) -> dict[str, Any] | None:
        """Restore the next project, or return None when no step remains."""
        self.close_group()
        if not self._redo:
            return None
        self._undo.append(self._current)
        self._current = self._redo.pop()
        return self.current_document

    def reset(self, document: dict[str, Any]) -> None:
        """Start a clean project session and discard all previous history."""
        snapshot = deepcopy(document)
        self._current = snapshot
        self._saved = snapshot
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self.close_group()

    def mark_saved(self, document: dict[str, Any] | None = None) -> None:
        """Set the successfully saved document as the new, empty baseline.

        Call this only after persistence succeeds. Failed or cancelled saves
        should leave the history intact (``close_group`` may still be called).
        """
        self.reset(self._current if document is None else document)

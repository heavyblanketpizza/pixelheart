"""An original wood-and-parchment birthday board for the valley's four seasons."""

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout, QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from pixelheart_core.birthdays import (
    DAYS_PER_SEASON, SEASONS, birthdays_on, festivals_on, festivals_for_season,
)
from .calendar_art import draw_lettering, draw_motif, draw_wood_frame, lettering_width
from .widgets import label


class CalendarBoard(QFrame):
    def paintEvent(self, event):
        painter = QPainter(self)
        draw_wood_frame(painter, self.rect())


class SeasonPicker(QComboBox):
    """A pixel-lettered plaque with the usual combo box keyboard and popup behavior."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(234, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        season = self.currentData()
        if self.underMouse() or self.hasFocus():
            painter.fillRect(self.rect().adjusted(2, 2, -2, -2), QColor("#f9efd9"))
        if season:
            width = lettering_width(season, 3)
            left = (self.width() - width - 46) // 2
            draw_motif(painter, season, left, 12)
            draw_lettering(painter, season, left + 36, 15, 3, "#e2d0ad")
            draw_lettering(painter, season, left + 36, 13, 3, "#715638")
        for row in range(3):
            painter.fillRect(self.width() - 16 + row * 2, 22 + row * 2, 10 - row * 4, 2, QColor("#a68b62"))
        if self.hasFocus():
            painter.setPen(QPen(QColor("#71815a"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self.rect().adjusted(2, 2, -3, -3))


class CalendarArrow(QPushButton):
    def __init__(self, direction, callback):
        super().__init__()
        self.direction = direction
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(callback)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#e8d6b1" if self.isDown() else "#fff5de" if self.underMouse() else "#f7ecd3"))
        painter.setPen(QPen(QColor("#71815a" if self.hasFocus() else "#c6ac81"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        for row in range(5):
            offset = abs(2 - row) * 2
            x = 11 + offset if self.direction < 0 else 19 - offset
            painter.fillRect(x, 12 + row * 2, 3, 2, QColor("#826747"))


class CalendarDay(QPushButton):
    """Native button semantics, painted as a paper square rather than a form field."""

    def __init__(self, day, parent=None):
        super().__init__(parent)
        self.day = day
        self.names = ()
        self.festivals = ()
        self.setObjectName("calendarDay")
        self.setCheckable(True)
        self.setMinimumWidth(54)
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def show_date(self, season, day, selected):
        self.day = day
        self.names = names = birthdays_on(season, day)
        self.festivals = festivals = festivals_on(season, day)
        self.setProperty("occupied", bool(names))
        self.setChecked(selected)
        self.setEnabled(not names)
        self.setCursor(Qt.CursorShape.ArrowCursor if names else Qt.CursorShape.PointingHandCursor)
        date = f"{season.title()} {day}"
        description = [date]
        if names:
            description.append(f"Birthday: {', '.join(names)}")
            description.append("This birthday is reserved. Choose an open day.")
        else:
            description.append("No existing NPC birthday. Available to choose.")
        if festivals:
            description.append("Festival: " + ", ".join(festivals))
        if selected:
            description.append("Your selected birthday.")
        self.setAccessibleName(". ".join(description))
        self.setToolTip("\n".join(description))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        selected = self.isChecked()
        conflict = selected and bool(self.names)
        hovered = self.isEnabled() and self.underMouse()
        paper = "#f8e5d9" if conflict else "#e9eedb" if selected else "#fff3d7" if hovered else "#f6ecd8" if self.names else "#fff8e9"
        painter.fillRect(self.rect(), QColor(paper))
        painter.fillRect(0, 0, self.width(), 1, QColor("#fffcf2"))
        if selected or hovered or self.hasFocus():
            border = "#b7755b" if conflict else "#819564" if selected else "#c5a572"
            painter.setPen(QPen(QColor(border), 2 if selected else 1))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        if self.hasFocus():
            painter.setPen(QPen(QColor("#687a50"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self.rect().adjusted(4, 4, -5, -5))
        draw_lettering(painter, str(self.day), 9, 8, 3, "#8b7453")
        compact = self.height() < 84
        motif = "gift" if self.names else "heart" if selected else "flag" if self.festivals else None
        if motif:
            draw_motif(painter, motif, (self.width() - 24) // 2, 24 if compact else 30, 2)
        if self.festivals and (self.names or selected):
            draw_motif(painter, "flag", self.width() - 18, 6, 1)
        caption = ", ".join(self.names) if self.names else "Your birthday" if selected else _festival_caption(self.festivals)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(QColor("#775e45" if self.names else "#526640" if selected else "#876b44"))
        caption = painter.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, self.width() - 10)
        painter.drawText(QRect(5, 49 if compact else 62, self.width() - 10, 20), Qt.AlignmentFlag.AlignCenter, caption)


def _festival_caption(festivals):
    """Short tile captions; tooltips and the season notes retain the full names."""
    if not festivals:
        return ""
    return {
        "Dance of the Moonlight Jellies": "Moonlight Jellies",
        "Feast of the Winter Star": "Winter Star",
        "Stardew Valley Fair": "Valley Fair",
        "Festival of Ice": "Ice Festival",
        "Desert Festival": "Desert Fest.",
    }.get(festivals[0], festivals[0])


class CalendarLegend(QWidget):
    def __init__(self, motif, text):
        super().__init__()
        self.motif = motif
        row = QHBoxLayout(self)
        row.setContentsMargins(28, 0, 0, 0)
        row.addWidget(label(text, "calendarLegend"))
        self.setMinimumHeight(22)

    def paintEvent(self, event):
        painter = QPainter(self)
        draw_motif(painter, self.motif, 0, (self.height() - 24) // 2, 2)


class BirthdayCalendar(QWidget):
    """Browsing never edits a birthday; only choosing a day emits a change."""

    date_changed = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_season = "spring"
        self.selected_day = 1
        self.setAccessibleName("Birthday calendar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet("""
            QFrame#birthdayGrid { background: #d0ba91; border: 1px solid #bfa279; border-radius: 0; }
            QLabel#calendarWeekday { background: #ecddbd; color: #705d40; padding: 8px 0; font-size: 11px; font-weight: bold; }
            QLabel#calendarLegend, QLabel#calendarNote { color: #796b56; font-size: 11px; }
            QLabel#birthdaySelection { color: #52663f; font-family: Georgia; font-size: 21px; }
            QLabel#birthdaySelection[conflict="true"] { color: #965d46; }
            QLabel#birthdayStatus { color: #6e775d; font-size: 12px; }
            QLabel#birthdayStatus[conflict="true"] { color: #965d46; }
            QFrame#birthdayReceipt { background: #eef1e4; border: 1px solid #d4dcc3; border-radius: 3px; }
            QFrame#birthdayReceipt[conflict="true"] { background: #f8ebe3; border-color: #dfbeac; }
            QLabel#calendarFestivalDate { color: #876b44; font-size: 12px; font-weight: bold; }
            QLabel#calendarFestivalName { color: #76654f; font-size: 12px; }
            QLabel#calendarDetailsHeading { color: #796b56; font-size: 10px; font-weight: bold; letter-spacing: 1px; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.content_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.content_layout.setSpacing(20)
        root.addLayout(self.content_layout)
        self._wide = False

        left = QWidget()
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        paper = CalendarBoard()
        paper.setObjectName("birthdayCalendar")
        paper_layout = QVBoxLayout(paper)
        paper_layout.setContentsMargins(17, 13, 17, 18)
        paper_layout.setSpacing(9)
        navigation = QHBoxLayout()
        self.previous_button = CalendarArrow(-1, lambda: self._browse(-1))
        self.previous_button.setAccessibleName("Previous season")
        self.previous_button.setToolTip("Previous season")
        self.next_button = CalendarArrow(1, lambda: self._browse(1))
        self.next_button.setAccessibleName("Next season")
        self.next_button.setToolTip("Next season")
        self.season_picker = SeasonPicker()
        self.season_picker.setObjectName("calendarSeason")
        self.season_picker.setAccessibleName("Calendar season to view")
        for season in SEASONS:
            self.season_picker.addItem(season.title(), season)
        navigation.addWidget(self.previous_button)
        navigation.addStretch()
        navigation.addWidget(self.season_picker)
        navigation.addStretch()
        navigation.addWidget(self.next_button)
        paper_layout.addLayout(navigation)

        grid_frame = QFrame()
        grid_frame.setObjectName("birthdayGrid")
        grid = QGridLayout(grid_frame)
        grid.setContentsMargins(1, 1, 1, 1)
        grid.setSpacing(1)
        for column, weekday in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            caption = label(weekday, "calendarWeekday")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(caption, 0, column)
            grid.setColumnStretch(column, 1)
        self.day_buttons = {}
        for day in range(1, DAYS_PER_SEASON + 1):
            day_button = CalendarDay(day)
            day_button.clicked.connect(lambda checked=False, d=day: self._select(d))
            day_button.installEventFilter(self)
            grid.addWidget(day_button, 1 + (day - 1) // 7, (day - 1) % 7)
            self.day_buttons[day] = day_button
        paper_layout.addWidget(grid_frame)
        left_layout.addWidget(paper)
        legend = QHBoxLayout()
        legend.setContentsMargins(5, 0, 5, 0)
        legend.setSpacing(18)
        for motif, text in (("gift", "NPC birthday"), ("heart", "Your birthday"), ("flag", "Festival")):
            legend.addWidget(CalendarLegend(motif, text))
        legend.addStretch()
        left_layout.addLayout(legend)
        self.content_layout.addWidget(left, 1, Qt.AlignmentFlag.AlignTop)

        self.details = QWidget()
        details_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.details)
        self.details_layout = details_layout
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(18)
        self.receipt = QFrame()
        self.receipt.setObjectName("birthdayReceipt")
        receipt_layout = QVBoxLayout(self.receipt)
        receipt_layout.setContentsMargins(16, 16, 16, 16)
        receipt_layout.setSpacing(9)
        receipt_layout.addWidget(label("SELECTED BIRTHDAY", "calendarDetailsHeading"))
        self.selection_label = label("", "birthdaySelection", True)
        receipt_layout.addWidget(self.selection_label)
        self.conflict_label = label("", "birthdayStatus", True)
        receipt_layout.addWidget(self.conflict_label)
        details_layout.addWidget(self.receipt, 1, Qt.AlignmentFlag.AlignTop)
        festivals = QWidget()
        festival_layout = QVBoxLayout(festivals)
        festival_layout.setContentsMargins(0, 0, 0, 0)
        festival_layout.setSpacing(14)
        self.festival_heading = label("", "calendarDetailsHeading")
        festival_layout.addWidget(self.festival_heading)
        self.festival_rows = QVBoxLayout()
        self.festival_rows.setSpacing(13)
        festival_layout.addLayout(self.festival_rows)
        festival_layout.addWidget(label("Festival days are available unless they share an NPC birthday.", "calendarNote", True))
        festival_layout.addWidget(label("Base-game birthdays, including Leo. Modded NPCs are not included.", "calendarNote", True))
        details_layout.addWidget(festivals, 1, Qt.AlignmentFlag.AlignTop)
        self.content_layout.addWidget(self.details, 0, Qt.AlignmentFlag.AlignTop)
        self._shown_festivals = None

        self.season_picker.currentIndexChanged.connect(self._render)
        self.setFocusProxy(self.season_picker)
        self._render()

    def set_date(self, season, day):
        """Load the saved date exactly, including overlaps, without editing it."""
        if season not in SEASONS or type(day) is not int or not 1 <= day <= DAYS_PER_SEASON:
            raise ValueError("Birthday must use a valid season and day from 1 to 28.")
        self.selected_season, self.selected_day = season, day
        self.season_picker.setCurrentIndex(self.season_picker.findData(season))
        self._render()

    def focus_selected(self):
        self.season_picker.setCurrentIndex(self.season_picker.findData(self.selected_season))
        target = self.day_buttons[self.selected_day]
        (target if target.isEnabled() else self.season_picker).setFocus()

    def _browse(self, offset):
        self.season_picker.setCurrentIndex((self.season_picker.currentIndex() + offset) % len(SEASONS))

    def _select(self, day):
        season = self.season_picker.currentData()
        if birthdays_on(season, day):
            return
        changed = (season, day) != (self.selected_season, self.selected_day)
        self.selected_season, self.selected_day = season, day
        self._render()
        if changed:
            self.date_changed.emit(season, day)

    def _render(self):
        season = self.season_picker.currentData()
        for day, day_button in self.day_buttons.items():
            selected = (season, day) == (self.selected_season, self.selected_day)
            day_button.show_date(season, day, selected)
        date = f"{self.selected_season.title()} {self.selected_day}"
        self.selection_label.setText(date)
        names = birthdays_on(self.selected_season, self.selected_day)
        for widget in (self.conflict_label, self.selection_label, self.receipt):
            widget.setProperty("conflict", bool(names))
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if names:
            self.conflict_label.setText(f"Shared birthdays are not allowed. This date belongs to {', '.join(names)}. Choose an open day before exporting.")
        else:
            self.conflict_label.setText("An open day — no existing NPC birthday.")
        self._render_festivals(season)

    def _render_festivals(self, season):
        if self._shown_festivals == season:
            return
        self._shown_festivals = season
        self.festival_heading.setText(f"{season.upper()} FESTIVALS")
        while self.festival_rows.count():
            row = self.festival_rows.takeAt(0).widget()
            row.hide()
            row.deleteLater()
        grouped = {}
        for day, names in festivals_for_season(season).items():
            for name in names:
                grouped.setdefault(name, []).append(day)
        for name, days in grouped.items():
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            date = str(days[0]) if len(days) == 1 else f"{days[0]}–{days[-1]}"
            date_label = label(date, "calendarFestivalDate")
            date_label.setFixedWidth(40)
            layout.addWidget(date_label, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(label(name, "calendarFestivalName", True), 1)
            self.festival_rows.addWidget(row)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.width() >= 900
        if wide != self._wide:
            self._wide = wide
            self.details.setMaximumWidth(224 if wide else 16777215)
            self.details.setMinimumWidth(224 if wide else 0)
            self.content_layout.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
            self.details_layout.setDirection(QBoxLayout.Direction.TopToBottom if wide else QBoxLayout.Direction.LeftToRight)
            for day_button in self.day_buttons.values():
                day_button.setFixedHeight(90 if wide else 72)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and watched in self.day_buttons.values():
            step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
                    Qt.Key.Key_Up: -7, Qt.Key.Key_Down: 7}.get(event.key())
            if step:
                day = next(day for day, widget in self.day_buttons.items() if widget is watched) + step
                while 1 <= day <= DAYS_PER_SEASON:
                    if self.day_buttons[day].isEnabled():
                        self.day_buttons[day].setFocus()
                        break
                    day += step
                return True
        return super().eventFilter(watched, event)

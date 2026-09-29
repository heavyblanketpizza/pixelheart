"""A character's journal page: who they are, what's done, and what's next."""
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from pixelheart_core.progress import hearts_earned, next_step
from pixelheart_core.projects import ProjectError
from .pixel_widgets import HeartMeter, PixelTitle, PortraitFrame
from .widgets import button, card, label


class OverviewPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.step_buttons, self.step_status, self.step_hearts = {}, {}, {}
        self._next_key = None
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        profile, content = card()
        profile.setObjectName("profile")
        profile.setMinimumWidth(200)
        profile.setMaximumWidth(290)
        self.portrait = PortraitFrame(112)
        content.addWidget(self.portrait)
        self.name = PixelTitle("", 2)
        content.addWidget(self.name)
        self.tagline = label("", "muted", True)
        self.birthday = label("", "muted")
        content.addWidget(self.tagline)
        content.addWidget(self.birthday)
        content.addSpacing(6)
        content.addWidget(label("HEARTS EARNED", "eyebrow"))
        self.meter = HeartMeter(8, 0)
        content.addWidget(self.meter)
        self.hearts_text = label("", "muted")
        content.addWidget(self.hearts_text)
        content.addStretch()
        root.addWidget(profile)

        column = QVBoxLayout()
        column.setSpacing(14)
        next_card, next_content = card()
        next_content.addWidget(label("NEXT UP", "eyebrow"))
        self.next_title = label("", "sectionTitle", True)
        next_content.addWidget(self.next_title)
        actions = QHBoxLayout()
        self.next_button = button("Let's go", self.open_next, "primary")
        actions.addWidget(self.next_button)
        actions.addStretch()
        self.review_button = button("Review checks", lambda: self.window.open_section("export"), "quiet")
        actions.addWidget(self.review_button)
        next_content.addLayout(actions)
        self.blockers = label("", "notice", True)
        next_content.addWidget(self.blockers)
        column.addWidget(next_card)

        steps_card, steps_content = card("Your character's journey",
                                         "Each finished step earns a heart. Home is optional.")
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(8)
        steps_content.addLayout(self._grid)
        column.addWidget(steps_card)
        column.addStretch()
        root.addLayout(column, 1)

    def _ensure_rows(self, steps):
        if self.step_buttons:
            return
        for row, step in enumerate(steps):
            heart = HeartMeter(1, 0)
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(label(step["title"]))
            status = label("", "muted", True)
            text.addWidget(status)
            open_button = button("Open", lambda _=False, key=step["key"]: self.window.open_section(key))
            open_button.setAccessibleName(f"Open {step['title']}")
            self._grid.addWidget(heart, row, 0)
            self._grid.addLayout(text, row, 1)
            self._grid.addWidget(open_button, row, 2)
            self.step_hearts[step["key"]] = heart
            self.step_status[step["key"]] = status
            self.step_buttons[step["key"]] = open_button
        self._grid.setColumnStretch(1, 1)

    def refresh(self, steps, blockers=None):
        self._ensure_rows(steps)
        character = self.window.document["character"]
        self.name.setText(character.get("name") or "Your character")
        self.tagline.setText(character.get("tagline") or character.get("occupation") or "A story waiting to be told.")
        self.birthday.setText(f"Birthday · {str(character.get('season', 'spring')).title()} {character.get('day', 1)}")
        try:
            portrait = self.window.resolved_artwork("portrait")
        except ProjectError:
            portrait = None
        self.portrait.set_portrait(portrait)
        earned = hearts_earned(steps)
        self.meter.set_value(earned, len(steps))
        self.hearts_text.setText(f"{earned} of {len(steps)} hearts")
        for step in steps:
            self.step_hearts[step["key"]].set_value(1 if step["done"] else 0)
            self.step_status[step["key"]].setText(step["status"])
        upcoming = next_step(steps)
        self._next_key = upcoming["key"] if upcoming else "export"
        self.next_title.setText(upcoming["next_action"] if upcoming
                                else "Everything's ready. Go say hello in Stardew Valley!")
        if blockers is not None:
            errors = sum(1 for issue in blockers if issue.get("level") == "error")
            self.blockers.setText(
                f"{errors} thing{'s' if errors != 1 else ''} to fix before they can move in."
                if errors else "Nothing is blocking them. You can put them in your game any time.")
        self.blockers.setVisible(blockers is not None)
        self.review_button.setVisible(blockers is not None)

    def open_next(self):
        self.window.open_section(self._next_key or "identity")

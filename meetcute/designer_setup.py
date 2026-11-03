"""Choose where the AI designer's words come from: nowhere, Anthropic, or a local model server."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QRadioButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from meetcute_designer.config import DEFAULTS, DesignerConfig, PROVIDERS, make_provider, url_is_loopback, validate_base_url
from meetcute_designer.providers import ProviderError

from .widgets import button, label


NOTICE = ("Your character's text is sent to this provider while you chat. "
          "Keys are stored in MEETC#TE's settings as plain text.")
ANTHROPIC_KEY_HINT = "Leave blank to use the ANTHROPIC_API_KEY environment variable."
LOCAL_MODEL_HINT = ("Run llama.cpp's llama-server with --jinja, or MLX's mlx_lm.server; both listen at port 8080. "
                    "Test connection shows the model id the server reports: llama-server accepts any name, "
                    "mlx_lm.server needs the model path. Pick a model that can call tools.")
UNENCRYPTED = "This address isn't on this computer, so the connection isn't encrypted."
PROVIDER_TITLES = {"off": "Off", "anthropic": "Anthropic (Claude)", "local": "Local model server"}
CHECK_WAIT_MS = 12000
# Connection checks outlive their dialog so closing it never destroys a running thread.
_PENDING = set()


def pending_checks():
    return [thread for thread in _PENDING if thread.isRunning()]


def wait_for_checks(timeout_ms=CHECK_WAIT_MS):
    """Let running checks end before the application goes away (each is bounded by its own timeout)."""
    for thread in list(_PENDING):
        thread.wait(timeout_ms)


def _release(thread):
    _PENDING.discard(thread)
    thread.deleteLater()


class ConnectionCheck(QThread):
    """Ask the chosen provider whether it answers, off the GUI thread."""

    result = Signal(str, bool)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self):
        try:
            self.result.emit(make_provider(self.config).check(), True)
        except ProviderError as exc:
            self.result.emit(exc.message, False)
        except Exception as exc:  # a provider bug must still report, not hang the dialog
            self.result.emit(f"The provider returned an error: {exc}", False)


def _password_field(text, placeholder):
    field = QLineEdit(text)
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setPlaceholderText(placeholder)
    return field


class DesignerSetupDialog(QDialog):
    def __init__(self, parent, config: DesignerConfig, *, inline: bool = False):
        super().__init__(parent)
        self.inline = inline
        self.check_thread = None
        self._url_error = None
        self.setWindowTitle("Set up AI designer")
        self.resize(580, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(12)
        root.addWidget(label("Set up AI designer", "title", True))
        root.addWidget(label("Choose who writes with you. Nothing is sent anywhere until you pick a provider.", "muted", True))

        choices = QHBoxLayout()
        group = QButtonGroup(self)
        self.provider_buttons = {}
        for key in PROVIDERS:
            radio = QRadioButton(PROVIDER_TITLES[key])
            radio.setAccessibleName(f"Provider {PROVIDER_TITLES[key]}")
            group.addButton(radio)
            choices.addWidget(radio)
            self.provider_buttons[key] = radio
        choices.addStretch()
        root.addLayout(choices)

        self.pages = QStackedWidget()
        off_page = QWidget()
        QVBoxLayout(off_page).addWidget(label("The AI designer stays out of the way.", "hint", True))
        self.pages.addWidget(off_page)

        anthropic_page = QWidget()
        anthropic_form = QFormLayout(anthropic_page)
        self.anthropic_model = QLineEdit(config.anthropic_model)
        self.anthropic_model.setAccessibleName("Anthropic model")
        self.anthropic_key = _password_field(config.anthropic_api_key, "sk-ant-…")
        self.anthropic_key.setAccessibleName("Anthropic API key")
        anthropic_form.addRow("Model", self.anthropic_model)
        anthropic_form.addRow("API key", self.anthropic_key)
        anthropic_form.addRow("", label(ANTHROPIC_KEY_HINT, "hint", True))
        self.pages.addWidget(anthropic_page)

        local_page = QWidget()
        local_form = QFormLayout(local_page)
        self.local_url = QLineEdit(config.local_base_url)
        self.local_url.setAccessibleName("Local server address")
        self.local_model = QLineEdit(config.local_model)
        self.local_model.setAccessibleName("Local model")
        self.local_model.setPlaceholderText("mlx-community/Qwen2.5-7B-Instruct-4bit")
        self.local_key = _password_field(config.local_api_key, "Only if your server asks for one")
        self.local_key.setAccessibleName("Local server key")
        local_form.addRow("Address", self.local_url)
        local_form.addRow("Model", self.local_model)
        local_form.addRow("", label(LOCAL_MODEL_HINT, "hint", True))
        local_form.addRow("Key", self.local_key)
        self.pages.addWidget(local_page)
        root.addWidget(self.pages)

        self.url_notice = label("", "notice", True)
        self.url_notice.hide()
        root.addWidget(self.url_notice)
        self.notice = label(NOTICE, "notice", True)
        root.addWidget(self.notice)

        check_row = QHBoxLayout()
        self.check_button = button("Test connection", self.run_check, "quiet")
        check_row.addWidget(self.check_button)
        self.check_result = label("", "muted", True)
        check_row.addWidget(self.check_result, 1)
        root.addLayout(check_row)
        root.addStretch()

        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Cancel", self.reject))
        self.save_button = button("Save", self.accept, "primary")
        self.save_button.setDefault(True)
        actions.addWidget(self.save_button)
        root.addLayout(actions)

        for key, radio in self.provider_buttons.items():
            radio.toggled.connect(self._provider_changed)
        self.local_url.textChanged.connect(self._validate_url)
        self.provider_buttons[config.provider if config.provider in PROVIDERS else "off"].setChecked(True)
        self._provider_changed()

    def selected_provider(self) -> str:
        return next((key for key, radio in self.provider_buttons.items() if radio.isChecked()), "off")

    def _provider_changed(self, *_):
        self.pages.setCurrentIndex(PROVIDERS.index(self.selected_provider()))
        self._validate_url()

    def _validate_url(self, *_):
        self._url_error = None
        if self.selected_provider() != "local":
            self.url_notice.hide()
            return
        try:
            url = validate_base_url(self.local_url.text())
        except ValueError as exc:
            self._url_error = str(exc)
            self.url_notice.setText(self._url_error)
            self.url_notice.show()
            return
        if url.startswith("http://") and not url_is_loopback(url):
            self.url_notice.setText(UNENCRYPTED)
            self.url_notice.show()
        else:
            self.url_notice.hide()

    def values(self) -> dict:
        url = self.local_url.text().strip()
        try:
            url = validate_base_url(url)
        except ValueError:
            pass
        values = dict(DEFAULTS)
        values.update({
            "designer/provider": self.selected_provider(),
            "designer/anthropic_model": self.anthropic_model.text().strip() or DEFAULTS["designer/anthropic_model"],
            "designer/anthropic_api_key": self.anthropic_key.text().strip(),
            "designer/local_base_url": url,
            "designer/local_model": self.local_model.text().strip(),
            "designer/local_api_key": self.local_key.text().strip(),
        })
        return values

    def accept(self):
        if self.selected_provider() == "local" and self._url_error:
            self.url_notice.setText(self._url_error)
            self.url_notice.show()
            return
        super().accept()

    def run_check(self):
        config = DesignerConfig.from_mapping(self.values())
        self.check_result.setText("Checking…")
        if self.inline:
            try:
                self._check_finished(make_provider(config).check(), True)
            except ProviderError as exc:
                self._check_finished(exc.message, False)
            return
        self.check_button.setEnabled(False)
        thread = ConnectionCheck(config)
        _PENDING.add(thread)
        thread.result.connect(self._check_finished)
        thread.finished.connect(self._check_done)
        thread.finished.connect(lambda thread=thread: _release(thread))
        self.check_thread = thread
        thread.start()

    def _check_finished(self, message, ok):
        self.check_result.setText(message)

    def _check_done(self):
        self.check_button.setEnabled(True)

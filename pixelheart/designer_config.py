"""Read and write the AI designer's settings through Pixelheart's own QSettings."""
from pixelheart_designer.config import DEFAULTS, DesignerConfig

from .game_import import game_import_settings


PANEL_VISIBLE_KEY = "designer/panel_visible"


def load_designer_config() -> DesignerConfig:
    settings = game_import_settings()
    return DesignerConfig.from_mapping({key: settings.value(key) for key in DEFAULTS})


def save_designer_config(values) -> None:
    """Write all six keys, normalized, so a partial mapping never leaves stale values behind."""
    settings = game_import_settings()
    for key, value in DesignerConfig.from_mapping(values).as_mapping().items():
        settings.setValue(key, value)
    settings.sync()

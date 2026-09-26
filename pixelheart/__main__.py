"""Run with ``python -m pixelheart [path/to/character.json]``."""

import argparse
import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .app import MainWindow
from .theme import apply_theme


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pixelheart — an offline Stardew Valley NPC editor")
    parser.add_argument("project", nargs="?", help="A project folder or character.json to open")
    parser.add_argument("--catalogue", help="Open a local development pack in Home → Catalogue development")
    parser.add_argument("--version", action="version", version=f"Pixelheart {__version__}")
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Pixelheart")
    app.setOrganizationName("Pixelheart")
    apply_theme(app)
    window = MainWindow(args.project, auto_download_icons=True)
    if args.catalogue:
        window.open_section("home")
        window.world.home_switch.setCurrentIndex(1)
        workshop = window.world.catalogue_workshop
        if workshop.load_pack(args.catalogue):
            workshop.refresh(window.project_file)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

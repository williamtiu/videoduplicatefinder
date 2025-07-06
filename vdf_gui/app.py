import sys
import logging
import os
from PyQt6.QtWidgets import QApplication

# Add project root to sys.path to allow imports from vdf_core & vdf_gui
# This is for direct execution of app.py and for finding vdf_core
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from vdf_gui.main_window import MainWindow

def setup_logging():
    # Basic logging configuration for the GUI application
    # Log messages will be captured by UILogHandler in MainWindowVM and displayed in Log tab
    # and also print to console if other handlers are configured (e.g. basicConfig default)
    logging.basicConfig(
        level=logging.DEBUG, # Set a base level; can be overridden by specific loggers
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout # Also log to console for debugging GUI startup
    )
    # You might want to set levels for specific noisy loggers, e.g.:
    # logging.getLogger('vdf_core.ffmpeg_utils').setLevel(logging.INFO)

from PyQt6.QtGui import QPixmapCache

def run_gui():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting VideoDuplicateFinder GUI application...")

    app = QApplication(sys.argv)

    # Set QPixmapCache limit (e.g., 100 MB)
    QPixmapCache.setCacheLimit(100 * 1024) # Kilobytes

    # Ensure vdf_core can be found if not installed as a package
    # This is already handled by the sys.path.insert above if running app.py directly.
    # If app.py is imported, the calling script should handle PYTHONPATH.

    try:
        main_win = MainWindow()
        main_win.show()
        logger.info("MainWindow shown.")
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"Unhandled exception in GUI application: {e}", exc_info=True)
        # Optionally show a critical error dialog to the user here
        sys.exit(1)


if __name__ == '__main__':
    run_gui()

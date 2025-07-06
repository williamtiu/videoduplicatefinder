from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap
from typing import Optional
import logging

# Assuming vdf_core is in PYTHONPATH or path adjusted
# Need a function from ffmpeg_utils to get preview thumbnail bytes
from vdf_core.ffmpeg_utils import get_media_info # For duration to pick a frame
# We'll need a new function like extract_preview_thumbnail_bytes in ffmpeg_utils
# For now, placeholder for the actual extraction logic:
from vdf_core.ffmpeg_utils import extract_thumbnail_image # Assuming this exists or will be created

logger = logging.getLogger(__name__)

class ThumbnailSignals(QObject):
    """
    Defines signals for ThumbnailLoader.
    Needed because QRunnable cannot inherit from QObject directly for signals.
    """
    finished = pyqtSignal() # Generic finished signal
    error = pyqtSignal(str) # Error message
    thumbnail_ready = pyqtSignal(str, QPixmap) # item_path, pixmap
    thumbnail_failed = pyqtSignal(str) # item_path for which thumbnail failed

class ThumbnailLoader(QRunnable):
    """
    A QRunnable worker to load a thumbnail in a background thread.
    """
    def __init__(self, item_path: str, target_size: QSize, position_sec: float, ffmpeg_settings: CoreSettings): # Added ffmpeg_settings
        super().__init__()
        self.item_path = item_path
        self.target_size = target_size
        self.position_sec = position_sec
        self.ffmpeg_settings = ffmpeg_settings # Store settings
        self.signals = ThumbnailSignals()

    def run(self):
        try:
            logger.debug(f"ThumbnailLoader starting for: {self.item_path}")

            # Determine position for videos if not specified (e.g., 10% or 1s)
            actual_position_sec = self.position_sec
            if actual_position_sec < 0 and not self.item_path.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")): # Crude check for video
                # For videos, if no position, try to get duration and pick a frame
                # This requires CoreSettings to be passed or a global settings accessor.
                # For now, let's assume a default position or that it's pre-calculated.
                # A better approach: pass CoreSettings or specific ffmpeg_settings to constructor.
                # Or, the caller (DuplicateItemVM) calculates this.
                # For this example, let's assume extract_thumbnail_image handles default position for images.
                # For videos, if position_sec is < 0, we might default to e.g. 1 second or 10%
                # This part of logic might be better handled by the caller preparing `actual_position_sec`.
                # For simplicity, if position_sec is -1, extract_thumbnail_image should handle it.
                pass


            # This function needs to be robust and return raw image bytes (e.g., PNG/JPEG)
            # Target width can be passed to ffmpeg to scale, or scale QPixmap later.
            # Passing target_width to ffmpeg is often more efficient.
            image_bytes: Optional[bytes] = extract_thumbnail_image(
                file_path=self.item_path,
                position_seconds=actual_position_sec,
                settings=self.ffmpeg_settings, # Pass stored settings
                output_size_wh=(self.target_size.width(), self.target_size.height())
            )

            if image_bytes:
                pixmap = QPixmap()
                if pixmap.loadFromData(image_bytes):
                    # Optionally scale if not done by ffmpeg, or if further refinement needed
                    # if pixmap.size() != self.target_size:
                    #     pixmap = pixmap.scaled(self.target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    logger.debug(f"Thumbnail loaded successfully for: {self.item_path}")
                    self.signals.thumbnail_ready.emit(self.item_path, pixmap)
                else:
                    logger.warning(f"Failed to load QPixmap from data for: {self.item_path}")
                    self.signals.thumbnail_failed.emit(self.item_path)
            else:
                logger.warning(f"extract_thumbnail_image returned no data for: {self.item_path}")
                self.signals.thumbnail_failed.emit(self.item_path)

        except Exception as e:
            logger.error(f"Error in ThumbnailLoader for {self.item_path}: {e}", exc_info=True)
            self.signals.error.emit(str(e))
            self.signals.thumbnail_failed.emit(self.item_path)
        finally:
            self.signals.finished.emit()

```

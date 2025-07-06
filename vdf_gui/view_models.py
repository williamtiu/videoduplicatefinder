from PyQt6.QtCore import QObject, pyqtSignal, QThreadPool, QSize, pyqtSlot
from PyQt6.QtGui import QPixmap, QPixmapCache
import os
import logging

# Assuming vdf_core is in PYTHONPATH or path adjusted
from vdf_core.file_entry import FileEntry, EntryFlags
from vdf_core.scan_engine import DuplicateItemGroup
from vdf_core.settings import CoreSettings # Needed for thumbnail extraction settings

from .workers import ThumbnailLoader # Import the worker

logger = logging.getLogger(__name__)

DEFAULT_THUMBNAIL_PREVIEW_POSITION_SEC = 1.0 # Default position for video preview thumbs if not specified
# Or calculate based on duration, e.g., 10%

class DuplicateItemVM_Py(QObject):
    """
    ViewModel for a single item displayed in the duplicates list/table.
    Wraps a FileEntry and adds GUI-specific properties.
    Handles asynchronous loading of its preview thumbnail.
    """
    # Signal to notify view (model) when thumbnail is ready or specific data changes
    thumbnail_ready_signal = pyqtSignal() # Emitted after _thumbnail_pixmap is set
    checked_state_changed_signal = pyqtSignal(bool)


    def __init__(self, file_entry: FileEntry, group_id: str, core_settings: CoreSettings, similarity_score: float = 0.0, parent=None):
        super().__init__(parent)
        self._file_entry = file_entry
        self._group_id = group_id
        self.core_settings = core_settings

        self._checked = False
        self._thumbnail_pixmap: QPixmap | None = None
        self._is_loading_thumbnail: bool = False
        self._best_match_flags: set[str] = set()
        self._similarity_to_reference: float = similarity_score


    @property
    def file_entry(self) -> FileEntry:
        return self._file_entry

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def path(self) -> str:
        return self._file_entry.path

    @property
    def filename(self) -> str:
        return self._file_entry.filename # os.path.basename(self._file_entry.path)

    @property
    def size_str(self) -> str:
        size_bytes = self._file_entry.file_size_bytes
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    @property
    def file_size_bytes(self) -> int:
        return self._file_entry.file_size_bytes


    @property
    def duration_str(self) -> str:
        if self._file_entry.media_info and self._file_entry.media_info.duration_seconds is not None:
            s = int(self._file_entry.media_info.duration_seconds)
            h = s // 3600
            m = (s % 3600) // 60
            s = s % 60
            return f"{h:02}:{m:02}:{s:02}"
        return "N/A"

    @property
    def duration_seconds(self) -> float | None:
        return self._file_entry.media_info.duration_seconds if self._file_entry.media_info else None


    @property
    def resolution_str(self) -> str:
        if self._file_entry.media_info and \
           self._file_entry.media_info.width is not None and \
           self._file_entry.media_info.height is not None:
            return f"{self._file_entry.media_info.width}x{self._file_entry.media_info.height}"
        return "N/A"

    # TODO: Add more properties for other FileEntry/MediaInfo fields as needed for display
    # e.g., similarity (would come from comparison result, not FileEntry directly)
    # For now, similarity might be a property of the group or stored elsewhere.
    # The C# DuplicateItemVM has ItemInfo which is a VDF.Core.ViewModels.DuplicateItem
    # That DuplicateItem wraps FileEntry and adds GroupId, Similarity, Best* flags.
    # We need to decide if ScanEngine.duplicates will store FileEntry or a new Python Core.DuplicateItem.
    # For now, assuming ScanEngine.DuplicateItemGroup.items are FileEntry.
    # Similarity is more a pair-wise or group property.

    @property
    def checked(self) -> bool:
        return self._checked

    @checked.setter
    def checked(self, value: bool):
        if self._checked != value:
            self._checked = value
            self.checked_state_changed_signal.emit(value)

    @property
    def thumbnail_pixmap(self) -> QPixmap | None:
        # Try QPixmapCache first
        cached_pixmap = QPixmapCache.find(self.path) # Use path as a key
        if cached_pixmap is not None and not cached_pixmap.isNull(): # Check if QPixmap is valid
            return cached_pixmap
        return self._thumbnail_pixmap # Return internal one if not in cache or if it's more up-to-date

    # No direct setter for thumbnail_pixmap from outside, it's set by _on_thumbnail_loaded

    @property
    def is_loading_thumbnail(self) -> bool:
        return self._is_loading_thumbnail

    def load_thumbnail_async(self, target_size: QSize):
        if self._is_loading_thumbnail:
            # logger.debug(f"Thumbnail for {self.filename} already loading.")
            return

        cached_pixmap = QPixmapCache.find(self.path)
        if cached_pixmap is not None and not cached_pixmap.isNull():
            if self._thumbnail_pixmap is None or self._thumbnail_pixmap.isNull(): # Update internal if not set
                 self._thumbnail_pixmap = cached_pixmap
            # logger.debug(f"Thumbnail for {self.filename} found in cache.")
            self.thumbnail_ready_signal.emit() # Notify model even if from cache
            return

        if self._thumbnail_pixmap is not None and not self._thumbnail_pixmap.isNull(): # Already loaded internally
            # logger.debug(f"Thumbnail for {self.filename} already loaded internally.")
            # QPixmapCache.insert(self.path, self._thumbnail_pixmap) # Ensure it's in cache
            self.thumbnail_ready_signal.emit()
            return

        logger.debug(f"Requesting async thumbnail load for {self.filename}")
        self._is_loading_thumbnail = True
        # self.display_data_changed.emit() # Model should emit dataChanged for placeholder

        position = DEFAULT_THUMBNAIL_PREVIEW_POSITION_SEC
        if not (self.file_entry.flags & EntryFlags.IS_IMAGE):
            if self.file_entry.media_info and self.file_entry.media_info.duration_seconds is not None:
                # Example: 10% into the video, but not more than 60s, not less than 1s
                pos_perc = self.file_entry.media_info.duration_seconds * 0.10
                position = min(max(pos_perc, 1.0), 60.0)
                if self.file_entry.media_info.duration_seconds < 1.0: # very short video
                    position = self.file_entry.media_info.duration_seconds * 0.5


        loader = ThumbnailLoader(
            item_path=self.path,
            target_size=target_size,
            position_sec=position,
            # Pass CoreSettings directly to loader if it needs more than just path/size/pos
            # For now, assuming extract_thumbnail_image in ffmpeg_utils takes CoreSettings
            # which ThumbnailLoader will need. So, loader needs settings.
            # This implies the loader's __init__ or the function it calls needs CoreSettings.
            # Let's assume ThumbnailLoader's run() calls a util that gets settings.
            # For now, we pass core_settings to the loader.
            ffmpeg_settings=self.core_settings
        )
        loader.signals.thumbnail_ready.connect(self._on_thumbnail_loaded)
        loader.signals.thumbnail_failed.connect(self._on_thumbnail_failed)
        loader.signals.finished.connect(self._on_loader_finished)

        QThreadPool.globalInstance().start(loader)

    @pyqtSlot(str, QPixmap)
    def _on_thumbnail_loaded(self, item_path: str, pixmap: QPixmap):
        if item_path == self.path: # Ensure it's for this item
            logger.debug(f"Thumbnail loaded for {self.filename}")
            self._thumbnail_pixmap = pixmap
            QPixmapCache.insert(self.path, pixmap) # Add to cache
            # self._is_loading_thumbnail = False # Set in _on_loader_finished
            self.thumbnail_ready_signal.emit() # Signal the model to refresh this item
        else:
            logger.warning(f"Received thumbnail for {item_path} but current item is {self.path}")


    @pyqtSlot(str)
    def _on_thumbnail_failed(self, item_path: str):
        if item_path == self.path:
            logger.warning(f"Thumbnail loading failed for {self.filename}")
            # self._is_loading_thumbnail = False # Set in _on_loader_finished
            # Optionally set a "failed" placeholder pixmap or leave as None
            # self.thumbnail_ready_signal.emit() # Still signal to refresh (e.g. to show placeholder)
        else:
            logger.warning(f"Received thumbnail failure for {item_path} but current item is {self.path}")


    @pyqtSlot()
    def _on_loader_finished(self):
        # logger.debug(f"ThumbnailLoader finished for {self.filename}")
        self._is_loading_thumbnail = False
        # If thumbnail_pixmap is still None here, it means it failed or was not set.
        # The thumbnail_ready_signal would have been emitted by success/failure slots already.
        # If we want a final "update display now that loading is done (success or fail)" signal:
        self.thumbnail_ready_signal.emit()


    @property
    def best_match_flags(self) -> set[str]:
        return self._best_match_flags

    @best_match_flags.setter
    def best_match_flags(self, flags: set[str]):
        if self._best_match_flags != flags:
            self._best_match_flags = flags
            self.display_data_changed.emit()

    # --- Methods for "IsBest" checks, similar to C# DuplicateItem ---
    # These would be used by GUI to highlight cells, e.g., via a converter or delegate
    def is_best_duration(self) -> bool: return "BestDuration" in self._best_match_flags
    def is_best_size(self) -> bool: return "BestSize" in self._best_match_flags # Smallest size
    def is_best_frame_size(self) -> bool: return "BestFrameSize" in self._best_match_flags # Resolution
    def is_best_fps(self) -> bool: return "BestFps" in self._best_match_flags
    def is_best_bitrate(self) -> bool: return "BestBitRate" in self._best_match_flags
    # Add more as needed (e.g. AudioSampleRate)


    # Placeholder for similarity, which is context-dependent (relative to what?)
    # In C#, DuplicateItem has a Similarity property. This implies it's often similarity
    # to the "reference" item in the group or an average.
    # For now, this VM doesn't store it directly. The table model might handle it.
    _similarity_to_reference: float = 0.0 # Example, not fully implemented how this is set

    @property
    def similarity_str(self) -> str:
        # This needs to be set from outside, e.g. by the model managing the group context
        return f"{self._similarity_to_reference * 100:.2f}%" if self._similarity_to_reference > 0 else "N/A"

    def set_similarity_to_reference(self, similarity: float):
        """Call this from the model or controller that knows the context."""
        if self._similarity_to_reference != similarity:
            self._similarity_to_reference = similarity
            self.display_data_changed.emit()


    # TODO: Add methods for thumbnail loading if it's done on-demand by the VM
    # e.g., load_thumbnail_async() which would use ffmpeg_utils and then set thumbnail_pixmap
    # For now, assume pixmap is pushed to this VM.

    is_generating_thumbnail = False # Flag for UI to show placeholder / spinner

```

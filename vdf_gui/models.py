from PyQt6.QtCore import QAbstractTableModel, Qt, QModelIndex, QVariant
from PyQt6.QtGui import QPixmap, QColor # For placeholder thumbnail and highlighting
from typing import List, Dict, Any, Optional

from .view_models import DuplicateItemVM_Py # Assuming view_models.py is in the same directory
from vdf_core.scan_engine import DuplicateItemGroup # For type hinting

# Define column indices for clarity
COL_CHECKED = 0
COL_THUMBNAIL = 1
COL_PATH = 2
COL_INFO_BLOCK = 3 # Duration/Type, Resolution, Size, Date Created
COL_FORMAT_BLOCK = 4 # Format, FPS, Bitrate
COL_AUDIO_BLOCK = 5 # Audio Format, Channel, Sample Rate
COL_SIMILARITY = 6
# Add more columns as needed

COLUMN_COUNT = 7 # Total number of columns

class DuplicatesTableModel(QAbstractTableModel):
    """
    A QAbstractTableModel to display groups of DuplicateItemVM_Py objects.
    This model will handle a list of lists, where each inner list is a group.
    However, QTableView is better with flat lists. For grouping, QTreeView is better.

    For QTableView, we can either:
    1. Flatten the list and indicate groups visually (e.g., background color, indent, special rows).
    2. Show only one group at a time, selected by the user.
    3. Use a QTreeView with a more complex model.

    This initial version will use a flattened list and store group information internally
    to help with visual grouping or sorting by group.
    It will display DuplicateItemVM_Py objects.
    """
    data_changed_for_row = pyqtSignal(int) # Emit row index when specific item needs full row refresh

    # Define headers - these will be displayed by the QTableView
    HEADERS = ["Select", "Thumbnail", "Path", "Details", "Video Format", "Audio Format", "Similarity"]
    # Standard thumbnail size for the table view cells
    TABLE_THUMBNAIL_SIZE = QSize(96, 54) # Approx 16:9, can be adjusted

    def __init__(self, core_settings: CoreSettings, parent=None): # Added core_settings
        super().__init__(parent)
        self.core_settings = core_settings
        self._items_with_groups: List[Tuple[str, DuplicateItemVM_Py]] = []
        self._group_id_map: Dict[str, List[DuplicateItemVM_Py]] = {}
        self._path_to_row_map: Dict[str, int] = {} # For quick lookup of row by item path


    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid(): # Should not happen for a flat table model
            return 0
        return len(self._items_with_groups)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return COLUMN_COUNT

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return QVariant() # Default for other roles/orientations

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < self.rowCount()):
            return QVariant()

        group_id, item_vm = self._items_with_groups[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if column == COL_PATH:
                return item_vm.path
            elif column == COL_INFO_BLOCK:
                created_str = item_vm.file_entry.date_created_utc.strftime('%Y-%m-%d %H:%M') if item_vm.file_entry.date_created_utc else "N/A"
                return f"Dur: {item_vm.duration_str}\nRes: {item_vm.resolution_str}\nSize: {item_vm.size_str}\nDate: {created_str}"
            elif column == COL_FORMAT_BLOCK:
                if item_vm.file_entry.flags & EntryFlags.IS_IMAGE: return "N/A (Image)"
                if item_vm.file_entry.media_info:
                    mi = item_vm.file_entry.media_info
                    pvs = mi.primary_video_stream
                    codec = pvs.codec_name if pvs and pvs.codec_name else "N/A"
                    fps = f"{pvs.frame_rate_avg:.2f}" if pvs and pvs.frame_rate_avg is not None else "N/A"

                    bitrate_kbps = "N/A"
                    if pvs and pvs.bit_rate:
                        bitrate_kbps = f"{pvs.bit_rate / 1000:.0f} kbps"
                    elif mi.bit_rate_bps:
                        bitrate_kbps = f"{mi.bit_rate_bps / 1000:.0f} kbps"
                    return f"Codec: {codec}\nFPS: {fps}\nBitrate: {bitrate_kbps}"
                return "Video Info N/A"
            elif column == COL_AUDIO_BLOCK:
                if item_vm.file_entry.flags & EntryFlags.IS_IMAGE: return "N/A (Image)"
                if item_vm.file_entry.media_info and item_vm.file_entry.media_info.audio_streams:
                    ast = item_vm.file_entry.media_info.audio_streams[0] # Primary audio
                    codec = ast.codec_name or "N/A"
                    channels = ast.channel_layout or (f"{ast.channels} ch" if ast.channels else "N/A")
                    sample_rate = f"{ast.sample_rate} Hz" if ast.sample_rate else "N/A"
                    return f"Codec: {codec}\nChannels: {channels}\nRate: {sample_rate}"
                return "Audio Info N/A"
            elif column == COL_SIMILARITY:
                # Similarity is tricky. It's usually relative to other items in the group.
                # The model might need to calculate/retrieve this.
                # For now, using the placeholder from VM.
                return item_vm.similarity_str
            # Checkbox and Thumbnail are handled by other roles or delegates
            return QVariant()

        elif role == Qt.ItemDataRole.CheckStateRole and column == COL_CHECKED:
            return Qt.CheckState.Checked if item_vm.checked else Qt.CheckState.Unchecked

        elif role == Qt.ItemDataRole.DecorationRole and column == COL_THUMBNAIL:
            # Try to get from cache or already loaded VM pixmap
            pixmap = item_vm.thumbnail_pixmap # This getter now checks cache first
            if pixmap and not pixmap.isNull():
                return pixmap.scaled(self.TABLE_THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

            # If no pixmap and not already loading, trigger async load
            if not item_vm.is_loading_thumbnail:
                # logger.debug(f"Requesting thumbnail for {item_vm.path} from model data() method")
                item_vm.load_thumbnail_async(self.TABLE_THUMBNAIL_SIZE) # VM needs core_settings

            # Return a placeholder while loading or if failed
            placeholder = QPixmap(self.TABLE_THUMBNAIL_SIZE)
            placeholder.fill(QColor("gainsboro")) # Use a slightly different color for loading
            # TODO: Could draw text "Loading..." or a spinner icon on placeholder via QPainter
            return placeholder

        elif role == Qt.ItemDataRole.ToolTipRole and column == COL_THUMBNAIL:
            return f"Thumbnail for {item_vm.filename}"

        elif role == Qt.ItemDataRole.BackgroundRole: # Visual cue for groups
            # Simple alternating background for groups; requires data to be sorted by group_id by view or proxy
            # This is a basic example. More robust grouping might need group header rows or tree view.
            current_group_id = group_id
            prev_group_id = self._items_with_groups[index.row()-1][0] if index.row() > 0 else None
            if prev_group_id is not None and current_group_id != prev_group_id:
                # This logic is tricky if not sorted by group.
                # A better way: assign a group_color_index when loading data.
                # For now, this is a placeholder for a more robust visual grouping.
                # if self._group_id_map.get(current_group_id, [])[0] == item_vm: # If it's the first item of a new group visually
                #     return QColor("lightyellow") # Example
                # Determine if this row starts a new group (assumes _items_with_groups is sorted by group_id)
                # This is a simple visual cue. More advanced would involve delegates or tree view.
                is_new_group = True # Assume first row is always start of a new group for coloring
                if index.row() > 0:
                    prev_group_id, _ = self._items_with_groups[index.row() - 1]
                    if group_id == prev_group_id:
                        is_new_group = False

                # Find which group number this is (0th, 1st, 2nd unique group, etc.)
                # This requires iterating or pre-calculating group indices.
                # For a simpler alternating color:
                group_ids_ordered = []
                for gid, _ in self._items_with_groups:
                    if gid not in group_ids_ordered:
                        group_ids_ordered.append(gid)

                try:
                    group_visual_index = group_ids_ordered.index(group_id)
                    if group_visual_index % 2 == 1: # Odd groups get a slightly different background
                        return QColor("#f0f0f5") # Light lavender/gray
                except ValueError:
                    pass # Should not happen if group_id is from _items_with_groups


        # Example: Highlighting "best" items using ForegroundRole for text color
        # This requires 'best_match_flags' to be populated on item_vm
        elif role == Qt.ItemDataRole.ForegroundRole:
            highlight_color = QColor("blue") # Example color for "best" items
            if column == COL_INFO_BLOCK: # Duration is part of this block
                if item_vm.is_best_duration(): return highlight_color
            # Add more conditions for other "best" flags and columns
            # e.g., if column corresponds to size and item_vm.is_best_size()
            pass

        return QVariant()

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.CheckStateRole or index.column() != COL_CHECKED:
            return False

        group_id, item_vm = self._items_with_groups[index.row()]

        if index.column() == COL_CHECKED:
            item_vm.checked = (value == Qt.CheckState.Checked.value) # value is int for CheckStateRole
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            return True

        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base_flags = super().flags(index)
        if not index.isValid():
            return base_flags

        if index.column() == COL_CHECKED:
            return base_flags | Qt.ItemFlag.ItemIsUserCheckable

        # Make other cells selectable, but not editable by default via setData
        return base_flags | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled


    def load_data(self, duplicate_groups: Dict[str, DuplicateItemGroup]):
        """
        Loads data from the ScanEngine's duplicate results.
        Flattens the groups into a list for the table view.
        """
        self.beginResetModel()
        self._items_with_groups.clear()
        self._group_id_map.clear()
        self._path_to_row_map.clear()

        # Sort group IDs for consistent display order if needed, or use as is
        # For stable visual grouping, it's good if items of the same group are contiguous.
        # This can be ensured by sorting _items_with_groups by group_id, then by some other criteria.
        # Or, if the view sorts by group_id, that's also fine.

        row_idx = 0
        for group_id, group_obj in duplicate_groups.items(): # dict iteration order is insertion order from Python 3.7+
            group_vms: List[DuplicateItemVM_Py] = []
            # group_obj.items_with_similarity is List[Tuple[FileEntry, float]]
            for file_entry, similarity_score in group_obj.items_with_similarity:
                item_vm = DuplicateItemVM_Py(
                    file_entry,
                    group_id,
                    self.core_settings,
                    similarity_score=similarity_score # Pass the similarity
                )

                if file_entry.path in group_obj.best_match_flags:
                    item_vm.best_match_flags = group_obj.best_match_flags[file_entry.path]

                item_vm.thumbnail_ready_signal.connect(
                    lambda item_path_closure=item_vm.path: self._on_item_thumbnail_ready(item_path_closure)
                )
                item_vm.checked_state_changed_signal.connect(
                    lambda checked_closure=item_vm.checked, item_path_closure=item_vm.path: self._on_item_checked_changed(item_path_closure, checked_closure)
                )

                self._items_with_groups.append((group_id, item_vm))
                self._path_to_row_map[item_vm.path] = row_idx
                row_idx += 1
                group_vms.append(item_vm)

            if group_vms:
                self._group_id_map[group_id] = group_vms

        # Example: Sort by group_id then path for consistent initial order
        # This helps with visual grouping if not using a tree view.
        self._items_with_groups.sort(key=lambda x: (x[0], x[1].path))
        # Update path_to_row_map after sorting
        for i, (_, item_vm) in enumerate(self._items_with_groups):
            self._path_to_row_map[item_vm.path] = i

        self.endResetModel()

    @pyqtSlot(str) # Assuming item_path is passed
    def _on_item_thumbnail_ready(self, item_path: str):
        if item_path in self._path_to_row_map:
            row = self._path_to_row_map[item_path]
            col_thumbnail = self.HEADERS.index("Thumbnail") # Or use COL_THUMBNAIL
            model_idx = self.index(row, col_thumbnail)
            self.dataChanged.emit(model_idx, model_idx, [Qt.ItemDataRole.DecorationRole])
            # logger.debug(f"Emitted dataChanged for thumbnail at row {row} for {item_path}")

    @pyqtSlot(str, bool) # Assuming item_path and new_checked_state
    def _on_item_checked_changed(self, item_path: str, checked_state: bool):
        # This is mostly for reacting to programmatic changes to checked state if needed.
        # Direct user interaction with checkbox calls setData, which updates VM and emits dataChanged.
        # If other logic changes VM's checked state, this ensures model updates view.
        if item_path in self._path_to_row_map:
            row = self._path_to_row_map[item_path]
            col_checked = self.HEADERS.index("Select") # Or use COL_CHECKED
            model_idx = self.index(row, col_checked)
            self.dataChanged.emit(model_idx, model_idx, [Qt.ItemDataRole.CheckStateRole])


    def get_item_vm_at(self, row: int) -> Optional[DuplicateItemVM_Py]:
        if 0 <= row < len(self._items_with_groups):
            return self._items_with_groups[row][1]
        return None

    def get_group_id_at(self, row: int) -> Optional[str]:
        if 0 <= row < len(self._items_with_groups):
            return self._items_with_groups[row][0]
        return None

    def get_items_in_group(self, group_id: str) -> List[DuplicateItemVM_Py]:
        return self._group_id_map.get(group_id, [])


    # TODO: Methods for sorting (if not using QSortFilterProxyModel)
    # TODO: Methods for filtering (if not using QSortFilterProxyModel)
    # For a QTableView, using QSortFilterProxyModel is often preferred.
    # This base model would then just hold the raw, flat data.

    def clear_all_checked_states(self):
        """Unchecks all items in the model."""
        # Need to emit dataChanged for all rows where checked state changes
        # A bit inefficient to emit for each, but correct.
        # beginResetModel/endResetModel is too broad if only check states change.
        # Consider emitting dataChanged for a range of rows if many are checked.

        changed_indexes: List[QModelIndex] = []
        for i, (_, item_vm) in enumerate(self._items_with_groups):
            if item_vm.checked:
                item_vm.checked = False # This will emit item_vm.checked_state_changed_signal
                # The model's _on_item_checked_changed slot will emit dataChanged for individual cells.
                # So, direct emission from here might be redundant IF that connection is robustly handled
                # for batch changes.
                # For directness and ensuring the view updates for all relevant cells:
                # changed_indexes.append(self.index(i, COL_CHECKED))

        # If many items changed, it's often better to signal a larger change
        # or rely on individual dataChanged signals from _on_item_checked_changed.
        # For now, individual signals from item_vm through _on_item_checked_changed should suffice.
        # If performance becomes an issue with many selections, this can be optimized
        # by emitting dataChanged for ranges.
        logger.info(f"Model: Cleared all checked states. Individual dataChanged events expected from ItemVMs.")
```

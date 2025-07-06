"""
Database management for VideoDuplicateFinder (Python version).
Initially using a JSON file for storage.
"""
import json
import os
import logging
from typing import Dict, Optional, List, Set

from .settings import CoreSettings
from .file_entry import FileEntry, EntryFlags # Assuming file_entry.py is in the same package

logger = logging.getLogger(__name__)

DEFAULT_DB_FILENAME = "vdf_database.json"
TEMP_DB_FILENAME = "vdf_database_temp.json"

class DatabaseManager:
    def __init__(self, settings: CoreSettings):
        self.settings = settings
        self._db_path = self._determine_db_path()
        self._temp_db_path = self._determine_db_path(is_temp=True)

        # In-memory representation: Dict[path, FileEntry]
        self._entries: Dict[str, FileEntry] = {}

        # Ensure the database directory exists
        db_dir = os.path.dirname(self._db_path)
        if db_dir: # Only create if not in current directory (db_dir would be empty)
            os.makedirs(db_dir, exist_ok=True)

        self.load_database()

    def _determine_db_path(self, is_temp: bool = False) -> str:
        filename = TEMP_DB_FILENAME if is_temp else DEFAULT_DB_FILENAME
        if self.settings.custom_database_folder and os.path.isdir(self.settings.custom_database_folder):
            return os.path.join(self.settings.custom_database_folder, filename)

        # Default: place it in a user-specific data directory or alongside the script for portability
        # For simplicity in CLI testing, let's put it in a 'data' subdirectory of the project root for now.
        # This can be made more robust later (e.g. using appdirs package).
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # Assumes database.py is in vdf_core
        data_dir = os.path.join(project_root, "data")
        return os.path.join(data_dir, filename)

    def load_database(self) -> bool:
        """
        Loads the database from the JSON file.
        Tries the main DB path, then temp if main is missing (e.g. from failed save).
        """
        load_path = self._db_path
        if not os.path.exists(load_path) and os.path.exists(self._temp_db_path):
            logger.info(f"Main database file not found, attempting to load from temp: {self._temp_db_path}")
            load_path = self._temp_db_path
        elif not os.path.exists(load_path):
            logger.info(f"No database file found at {load_path}. Starting with an empty database.")
            self._entries.clear()
            return True # Not an error, just no DB yet

        if os.path.exists(load_path) and os.path.getsize(load_path) == 0:
            logger.warning(f"Database file {load_path} is empty. Starting with an empty database.")
            self._entries.clear()
            if load_path == self._db_path: # If main DB was empty, remove it
                 try: os.remove(load_path)
                 except OSError as e: logger.error(f"Could not remove empty DB file {load_path}: {e}")
            return True

        try:
            with open(load_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            loaded_entries: Dict[str, FileEntry] = {}
            if isinstance(data, list): # Expecting a list of entry dicts
                for entry_dict in data:
                    try:
                        entry = FileEntry.from_dict(entry_dict)
                        loaded_entries[entry.path] = entry
                    except Exception as e:
                        logger.error(f"Error deserializing entry from database: {entry_dict.get('path', 'Unknown Path')}. Error: {e}", exc_info=self.settings.extended_ffmpeg_logging)
            else:
                logger.error(f"Database file {load_path} is not in the expected list format.")
                # Optionally, try to recover or start fresh. For now, start fresh if format is wrong.
                self._entries.clear()
                return False

            self._entries = loaded_entries
            logger.info(f"Successfully loaded {len(self._entries)} entries from {load_path}.")

            # If loaded from temp, try to rename temp to main
            if load_path == self._temp_db_path:
                try:
                    os.replace(self._temp_db_path, self._db_path) # os.replace is atomic
                    logger.info(f"Successfully restored database from {self._temp_db_path} to {self._db_path}")
                except OSError as e:
                    logger.error(f"Could not replace main DB with temp DB. Error: {e}")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from database file {load_path}: {e}", exc_info=True)
            # Consider renaming the corrupted DB file (e.g., to _DAMAGED.json)
            try:
                damaged_path = load_path + "_DAMAGED.json"
                os.rename(load_path, damaged_path)
                logger.info(f"Renamed corrupted database file to {damaged_path}")
            except OSError as rename_e:
                logger.error(f"Could not rename corrupted database file {load_path}: {rename_e}")
            self._entries.clear()
            return False
        except Exception as e:
            logger.error(f"Failed to load database from {load_path}: {e}", exc_info=True)
            self._entries.clear()
            return False

    def save_database(self) -> bool:
        """Saves the current in-memory database to the JSON file via a temporary file."""
        # Ensure the target directory exists
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
             os.makedirs(db_dir, exist_ok=True)

        entry_list_to_save = [entry.to_dict() for entry in self._entries.values()]

        try:
            with open(self._temp_db_path, 'w', encoding='utf-8') as f:
                json.dump(entry_list_to_save, f, indent=4) # Use indent for readability

            # Replace the main database file with the temporary one (atomic operation if possible)
            os.replace(self._temp_db_path, self._db_path)
            logger.info(f"Successfully saved {len(self._entries)} entries to {self._db_path}.")
            return True
        except Exception as e:
            logger.error(f"Failed to save database to {self._db_path} (temp path {self._temp_db_path}): {e}", exc_info=True)
            return False

    def get_entry(self, file_path: str) -> Optional[FileEntry]:
        abs_path = os.path.abspath(file_path)
        return self._entries.get(abs_path)

    def add_or_update_entry(self, entry: FileEntry):
        """Adds a new entry or updates an existing one based on its path."""
        if not entry.path: # Should not happen with FileEntry's __post_init__
            logger.warning("Attempted to add/update entry with no path.")
            return
        self._entries[entry.path] = entry # Path is absolute due to FileEntry's __post_init__
        # Consider auto-saving or batching saves, for now manual save_database() call needed.

    def remove_entry(self, file_path: str) -> bool:
        abs_path = os.path.abspath(file_path)
        if abs_path in self._entries:
            del self._entries[abs_path]
            return True
        return False

    def get_all_entries(self) -> List[FileEntry]:
        return list(self._entries.values())

    def get_all_paths(self) -> Set[str]:
        return set(self._entries.keys())

    def clear_database(self):
        self._entries.clear()
        logger.info("In-memory database cleared.")
        # Does not automatically save; call save_database() explicitly.
        # Or, remove the file if it exists
        if os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
                logger.info(f"Removed database file: {self._db_path}")
            except OSError as e:
                logger.error(f"Error removing database file {self._db_path}: {e}")
        if os.path.exists(self._temp_db_path): # Clean up temp file too
            try: os.remove(self._temp_db_path)
            except OSError: pass


    def update_entry_path(self, old_abs_path: str, new_abs_path: str) -> bool:
        """Updates the path of an entry. The FileEntry object's path must also be updated by the caller."""
        if old_abs_path == new_abs_path:
            return True # No change needed

        entry = self._entries.pop(old_abs_path, None)
        if entry:
            # The caller should ensure entry.path is already updated to new_abs_path
            if entry.path != new_abs_path:
                 logger.warning(f"Updating entry path in DB from {old_abs_path} to {new_abs_path}, but entry object still has path {entry.path}. Ensure entry.path is updated first.")
                 entry.path = new_abs_path # Force update if caller forgot
            self._entries[new_abs_path] = entry
            return True
        logger.warning(f"Could not update path: entry not found for old path {old_abs_path}")
        return False

    def blacklist_entry(self, file_path: str) -> bool:
        abs_path = os.path.abspath(file_path)
        entry = self.get_entry(abs_path)
        if entry:
            entry.flags |= EntryFlags.MANUALLY_EXCLUDED
            # self.add_or_update_entry(entry) # Implicitly updated as it's a reference
            logger.info(f"Entry blacklisted: {abs_path}")
            return True
        logger.warning(f"Could not blacklist: entry not found for path {abs_path}")
        return False

    def cleanup_database(self, existing_file_paths_on_disk: Optional[Set[str]] = None):
        """
        Removes entries for files that no longer exist on disk (if existing_file_paths_on_disk is provided)
        and entries with critical errors if settings.include_non_existing_files is False.
        Also removes entries with METADATA_ERROR or THUMBNAIL_ERROR if they have no thumbnails.
        """
        initial_count = len(self._entries)
        paths_to_remove: Set[str] = set()

        for path, entry in self._entries.items():
            exists_on_disk = existing_file_paths_on_disk is None or path in existing_file_paths_on_disk

            if not self.settings.include_non_existing_files and not exists_on_disk:
                paths_to_remove.add(path)
                logger.debug(f"Cleanup: Removing non-existing file (and not including non-existing): {path}")
                continue

            # C# logic: Database.RemoveWhere(a => !File.Exists(a.Path) || a.Flags.Any(EntryFlags.MetadataError | EntryFlags.ThumbnailError));
            # This seems to remove if (NOT exists) OR (has metadata error) OR (has thumbnail error).
            # Let's refine this. If we include non-existing, we only remove based on errors.
            # If we DON'T include non-existing, then non-existence is primary reason.

            # If we are keeping non-existing files, we only care about errors.
            # If we are NOT keeping non-existing files, the check above already handled it.
            # So, this part is about error flags, potentially independent of existence if include_non_existing_files is true.

            # Let's try to match C# more closely: remove if it has MetadataError OR ThumbnailError.
            # However, a ThumbnailError might be acceptable if some thumbnails still exist.
            # The C# code is quite aggressive.
            # A more nuanced approach: Remove if MetadataError is present (fundamental issue).
            # Remove if ThumbnailError is present AND no thumbnails were successfully generated.
            if entry.flags & EntryFlags.METADATA_ERROR:
                paths_to_remove.add(path)
                logger.debug(f"Cleanup: Removing entry with METADATA_ERROR: {path}")
                continue

            if entry.flags & EntryFlags.THUMBNAIL_ERROR and not entry.gray_bytes_data:
                # If it's flagged with thumbnail error AND has no thumbnail data at all
                paths_to_remove.add(path)
                logger.debug(f"Cleanup: Removing entry with THUMBNAIL_ERROR and no thumbnails: {path}")
                continue

        for path in paths_to_remove:
            self.remove_entry(path)

        removed_count = initial_count - len(self._entries)
        if removed_count > 0:
            logger.info(f"Database cleanup finished. Removed {removed_count} entries.")
        else:
            logger.info("Database cleanup finished. No entries removed.")
        # Caller should decide whether to save_database() after cleanup.

```

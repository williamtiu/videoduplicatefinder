import unittest
from unittest.mock import patch, mock_open, call # For mocking file I/O and os functions
import os
import json
import tempfile
import shutil

# Ensure vdf_core is in path for testing
import sys
project_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root_dir)

from vdf_core.settings import CoreSettings
from vdf_core.file_entry import FileEntry, EntryFlags, MediaInfo # For creating test entries
from vdf_core.database import DatabaseManager, DEFAULT_DB_FILENAME, TEMP_DB_FILENAME

# Mock __post_init__ for FileEntry to avoid file system access during these tests
original_file_entry_post_init = FileEntry.__post_init__
def mock_file_entry_post_init_for_db_tests(self_fe):
    if not hasattr(self_fe, 'path') or not self_fe.path: self_fe.path = os.path.abspath("dummy/path.vid")
    else: self_fe.path = os.path.abspath(self_fe.path) # Ensure it's absolute
    if not hasattr(self_fe, 'flags'): self_fe.flags = EntryFlags.NONE
    if not hasattr(self_fe, 'gray_bytes_data'): self_fe.gray_bytes_data = {}
    if self_fe.path.lower().endswith((".jpg", ".png", ".jpeg")):
        self_fe.flags |= EntryFlags.IS_IMAGE

FileEntry.__post_init__ = mock_file_entry_post_init_for_db_tests


class TestDatabaseManager(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory to act as the project root for database path calculations
        self.test_project_root = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.test_project_root, "data")
        # os.makedirs(self.data_dir, exist_ok=True) # DatabaseManager creates it

        # Patch _determine_db_path to use our temp data_dir
        # This is a bit tricky as it's called in __init__.
        # Instead, we can pass custom_database_folder to settings.
        self.settings = CoreSettings()
        self.settings.custom_database_folder = self.data_dir

        # Path for DatabaseManager to use, derived from settings
        self.db_path = os.path.join(self.data_dir, DEFAULT_DB_FILENAME)
        self.temp_db_path = os.path.join(self.data_dir, TEMP_DB_FILENAME)


    def tearDown(self):
        shutil.rmtree(self.test_project_root)
        FileEntry.__post_init__ = original_file_entry_post_init # Restore

    def _create_mock_entry(self, path_suffix: str, content_id: int) -> FileEntry:
        entry = FileEntry(path=f"test/{path_suffix}")
        entry.file_size_bytes = 1000 + content_id
        entry.thumb_config_signature = f"sig_{content_id}"
        entry.gray_bytes_data = {0.0: os.urandom(10)} # Add some dummy data
        return entry

    def test_init_and_paths(self):
        # Test if DB directory and paths are set up correctly
        # __init__ calls load_database, which might try to create data_dir
        with patch('vdf_core.database.os.path.exists', return_value=False): # Ensure it thinks no DB exists
             db_manager = DatabaseManager(self.settings)

        self.assertTrue(os.path.isdir(self.data_dir)) # Check if data_dir was created by db_manager
        self.assertEqual(db_manager._db_path, self.db_path)
        self.assertEqual(db_manager._temp_db_path, self.temp_db_path)
        self.assertEqual(len(db_manager._entries), 0) # Starts empty if no DB file

    def test_save_and_load_database(self):
        db_manager = DatabaseManager(self.settings) # Will try to load, should be empty
        self.assertEqual(len(db_manager._entries), 0)

        entry1 = self._create_mock_entry("vid1.mp4", 1)
        entry2 = self._create_mock_entry("img1.jpg", 2)
        entry2.flags |= EntryFlags.IS_IMAGE # Ensure flag is set

        db_manager.add_or_update_entry(entry1)
        db_manager.add_or_update_entry(entry2)
        self.assertTrue(db_manager.save_database())

        # Create a new manager to load the saved data
        db_manager_load = DatabaseManager(self.settings)
        self.assertEqual(len(db_manager_load._entries), 2)

        loaded_entry1 = db_manager_load.get_entry(entry1.path)
        loaded_entry2 = db_manager_load.get_entry(entry2.path)

        self.assertIsNotNone(loaded_entry1)
        self.assertIsNotNone(loaded_entry2)
        if loaded_entry1 and loaded_entry2: # For type checker
            self.assertEqual(loaded_entry1.file_size_bytes, entry1.file_size_bytes)
            self.assertEqual(loaded_entry1.thumb_config_signature, entry1.thumb_config_signature)
            self.assertTrue(loaded_entry2.flags & EntryFlags.IS_IMAGE)
            self.assertEqual(loaded_entry1.gray_bytes_data[0.0], entry1.gray_bytes_data[0.0])


    def test_load_database_empty_file(self):
        # Create an empty DB file
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.db_path, 'w') as f:
            pass # Create empty file

        db_manager = DatabaseManager(self.settings)
        self.assertEqual(len(db_manager._entries), 0)
        self.assertFalse(os.path.exists(self.db_path), "Empty DB file should be removed on load")

    @patch('vdf_core.database.json.load')
    def test_load_database_json_decode_error(self, mock_json_load):
        mock_json_load.side_effect = json.JSONDecodeError("mock error", "doc", 0)

        # Create a dummy (but non-empty) db file to trigger load attempt
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.db_path, 'w') as f:
            f.write("invalid json")

        db_manager = DatabaseManager(self.settings)
        self.assertEqual(len(db_manager._entries), 0) # Should be empty after error

        # Check if original corrupted DB was renamed
        damaged_db_path = self.db_path + "_DAMAGED.json"
        self.assertTrue(os.path.exists(damaged_db_path))
        self.assertFalse(os.path.exists(self.db_path)) # Original should be gone
        os.remove(damaged_db_path) # Clean up

    def test_load_database_from_temp_file(self):
        entry1 = self._create_mock_entry("vid_temp.mp4", 10)

        # Simulate a successful save to temp file, but main DB is missing
        os.makedirs(self.data_dir, exist_ok=True)
        if os.path.exists(self.db_path): os.remove(self.db_path) # Ensure main doesn't exist

        temp_data = [entry1.to_dict()]
        with open(self.temp_db_path, 'w', encoding='utf-8') as f:
            json.dump(temp_data, f)

        db_manager = DatabaseManager(self.settings) # This should load from temp
        self.assertEqual(len(db_manager._entries), 1)
        loaded_entry = db_manager.get_entry(entry1.path)
        self.assertIsNotNone(loaded_entry)
        if loaded_entry: self.assertEqual(loaded_entry.file_size_bytes, entry1.file_size_bytes)

        # Check if temp file was moved to main db path
        self.assertTrue(os.path.exists(self.db_path))
        self.assertFalse(os.path.exists(self.temp_db_path))


    def test_add_get_remove_entry(self):
        db_manager = DatabaseManager(self.settings)
        entry1 = self._create_mock_entry("add1.mkv", 3)

        self.assertIsNone(db_manager.get_entry(entry1.path))
        db_manager.add_or_update_entry(entry1)
        self.assertIsNotNone(db_manager.get_entry(entry1.path))
        self.assertEqual(db_manager.get_entry(entry1.path).file_size_bytes, entry1.file_size_bytes)

        self.assertTrue(db_manager.remove_entry(entry1.path))
        self.assertIsNone(db_manager.get_entry(entry1.path))
        self.assertFalse(db_manager.remove_entry(entry1.path)) # Already removed

    def test_get_all_entries_and_paths(self):
        db_manager = DatabaseManager(self.settings)
        entry1 = self._create_mock_entry("all1.mp4", 4)
        entry2 = self._create_mock_entry("all2.jpg", 5)
        db_manager.add_or_update_entry(entry1)
        db_manager.add_or_update_entry(entry2)

        all_entries = db_manager.get_all_entries()
        self.assertEqual(len(all_entries), 2)
        paths = {e.path for e in all_entries}
        self.assertIn(entry1.path, paths)
        self.assertIn(entry2.path, paths)

        all_paths = db_manager.get_all_paths()
        self.assertEqual(len(all_paths), 2)
        self.assertIn(entry1.path, all_paths)
        self.assertIn(entry2.path, all_paths)

    def test_clear_database(self):
        db_manager = DatabaseManager(self.settings)
        entry1 = self._create_mock_entry("clear.avi", 6)
        db_manager.add_or_update_entry(entry1)
        db_manager.save_database() # Make sure a file exists
        self.assertTrue(os.path.exists(self.db_path))

        db_manager.clear_database()
        self.assertEqual(len(db_manager._entries), 0)
        self.assertFalse(os.path.exists(self.db_path), "DB file should be removed by clear_database")

    def test_update_entry_path(self):
        db_manager = DatabaseManager(self.settings)
        old_path_suffix = "old_name.mp4"
        new_path_suffix = "new_name.mp4"
        entry = self._create_mock_entry(old_path_suffix, 7)
        old_abs_path = entry.path

        db_manager.add_or_update_entry(entry)
        self.assertIsNotNone(db_manager.get_entry(old_abs_path))

        # IMPORTANT: The caller of db_manager.update_entry_path is responsible for updating entry.path itself
        entry.path = os.path.abspath(f"test/{new_path_suffix}") # Update object's path
        new_abs_path = entry.path

        self.assertTrue(db_manager.update_entry_path(old_abs_path, new_abs_path))
        self.assertIsNone(db_manager.get_entry(old_abs_path))
        retrieved_entry = db_manager.get_entry(new_abs_path)
        self.assertIsNotNone(retrieved_entry)
        if retrieved_entry:
             self.assertEqual(retrieved_entry.path, new_abs_path)
             self.assertEqual(retrieved_entry.file_size_bytes, entry.file_size_bytes)

    def test_blacklist_entry(self):
        db_manager = DatabaseManager(self.settings)
        entry_path_suffix = "blacklist_me.mkv"
        entry = self._create_mock_entry(entry_path_suffix, 8)
        db_manager.add_or_update_entry(entry)

        self.assertFalse(entry.flags & EntryFlags.MANUALLY_EXCLUDED)
        self.assertTrue(db_manager.blacklist_entry(entry.path))

        blacklisted_entry = db_manager.get_entry(entry.path)
        self.assertIsNotNone(blacklisted_entry)
        if blacklisted_entry:
            self.assertTrue(blacklisted_entry.flags & EntryFlags.MANUALLY_EXCLUDED)

    def test_cleanup_database(self):
        db_manager = DatabaseManager(self.settings)

        # Entry that exists on disk (mocked)
        entry_ok = self._create_mock_entry("ok.mp4", 100)
        # Entry that does not exist on disk (mocked)
        entry_missing = self._create_mock_entry("missing.mp4", 101)
        # Entry with metadata error
        entry_meta_err = self._create_mock_entry("meta_err.mp4", 102)
        entry_meta_err.flags |= EntryFlags.METADATA_ERROR
        # Entry with thumbnail error and no thumbnails
        entry_thumb_err_no_data = self._create_mock_entry("thumb_err_empty.mp4", 103)
        entry_thumb_err_no_data.flags |= EntryFlags.THUMBNAIL_ERROR
        entry_thumb_err_no_data.gray_bytes_data = {}
        # Entry with thumbnail error but some (stale/partial) thumbnails
        entry_thumb_err_with_data = self._create_mock_entry("thumb_err_stale.mp4", 104)
        entry_thumb_err_with_data.flags |= EntryFlags.THUMBNAIL_ERROR
        entry_thumb_err_with_data.gray_bytes_data = {0.0: b"partial"}


        db_manager.add_or_update_entry(entry_ok)
        db_manager.add_or_update_entry(entry_missing)
        db_manager.add_or_update_entry(entry_meta_err)
        db_manager.add_or_update_entry(entry_thumb_err_no_data)
        db_manager.add_or_update_entry(entry_thumb_err_with_data)

        self.assertEqual(len(db_manager._entries), 5)

        # Scenario 1: Include non-existing files = False
        db_manager.settings.include_non_existing_files = False
        mock_existing_files_on_disk = {entry_ok.path, entry_meta_err.path, entry_thumb_err_no_data.path, entry_thumb_err_with_data.path}

        db_manager.cleanup_database(mock_existing_files_on_disk)
        # Expected to remove:
        # - entry_missing (not on disk)
        # - entry_meta_err (METADATA_ERROR)
        # - entry_thumb_err_no_data (THUMBNAIL_ERROR and no data)
        # Expected to keep:
        # - entry_ok
        # - entry_thumb_err_with_data (THUMBNAIL_ERROR but has some data, might be useful)
        self.assertIsNotNone(db_manager.get_entry(entry_ok.path))
        self.assertIsNone(db_manager.get_entry(entry_missing.path))
        self.assertIsNone(db_manager.get_entry(entry_meta_err.path))
        self.assertIsNone(db_manager.get_entry(entry_thumb_err_no_data.path))
        self.assertIsNotNone(db_manager.get_entry(entry_thumb_err_with_data.path))
        self.assertEqual(len(db_manager._entries), 2)

        # Reset for Scenario 2
        db_manager._entries.clear()
        db_manager.add_or_update_entry(entry_ok)
        db_manager.add_or_update_entry(entry_missing)
        db_manager.add_or_update_entry(entry_meta_err)
        db_manager.add_or_update_entry(entry_thumb_err_no_data)
        db_manager.add_or_update_entry(entry_thumb_err_with_data)

        # Scenario 2: Include non-existing files = True
        db_manager.settings.include_non_existing_files = True
        db_manager.cleanup_database(None) # Pass None for existing_file_paths, so existence isn't checked by this param
        # Expected to remove:
        # - entry_meta_err
        # - entry_thumb_err_no_data
        # Expected to keep:
        # - entry_ok
        # - entry_missing (because include_non_existing_files is True)
        # - entry_thumb_err_with_data
        self.assertIsNotNone(db_manager.get_entry(entry_ok.path))
        self.assertIsNotNone(db_manager.get_entry(entry_missing.path))
        self.assertIsNone(db_manager.get_entry(entry_meta_err.path))
        self.assertIsNone(db_manager.get_entry(entry_thumb_err_no_data.path))
        self.assertIsNotNone(db_manager.get_entry(entry_thumb_err_with_data.path))
        self.assertEqual(len(db_manager._entries), 3)


if __name__ == '__main__':
    unittest.main()

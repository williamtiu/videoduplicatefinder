import unittest
from unittest.mock import MagicMock, patch
import os
import sys
from typing import List, Tuple, Dict, Set

# Ensure vdf_core is in path for testing
project_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root_dir)

from vdf_core.scan_engine import ScanEngine, DuplicateItemGroup, ScanProgressEventArgs, OperationCanceledError
from vdf_core.settings import CoreSettings, ThumbnailPositionSetting, ThumbnailPositionType, get_thumbnail_config_signature
from vdf_core.database import DatabaseManager
from vdf_core.file_entry import FileEntry, MediaInfo, EntryFlags
from vdf_core.comparison import ComparisonOutcome

# Mock __post_init__ for FileEntry to avoid file system access during these tests
original_file_entry_post_init = FileEntry.__post_init__
def mock_file_entry_post_init_for_scan_engine_tests(self_fe):
    if not hasattr(self_fe, 'path') or not self_fe.path: self_fe.path = os.path.abspath("dummy/path.vid")
    else: self_fe.path = os.path.abspath(self_fe.path)
    if not hasattr(self_fe, 'flags'): self_fe.flags = EntryFlags.NONE
    if not hasattr(self_fe, 'gray_bytes_data'): self_fe.gray_bytes_data = {}
    if not hasattr(self_fe, 'filename'): # Ensure filename is set if path is
        self_fe.filename = os.path.basename(self_fe.path)


FileEntry.__post_init__ = mock_file_entry_post_init_for_scan_engine_tests

class TestScanEngineGrouping(unittest.TestCase):

    def _create_entry(self, name_id: str) -> FileEntry:
        # Path doesn't need to exist due to mocked __post_init__
        entry = FileEntry(path=f"/test/file_{name_id}.mp4")
        # Ensure filename is explicitly set if mock __post_init__ doesn't.
        entry.filename = f"file_{name_id}.mp4"
        return entry

    def test_build_duplicate_groups_no_pairs(self):
        settings = CoreSettings()
        db_manager_mock = MagicMock(spec=DatabaseManager)
        engine = ScanEngine(settings, db_manager_mock)

        engine._build_duplicate_groups([])
        self.assertEqual(len(engine.duplicates), 0)

    def test_build_duplicate_groups_single_pair(self):
        settings = CoreSettings()
        db_manager_mock = MagicMock(spec=DatabaseManager)
        engine = ScanEngine(settings, db_manager_mock)

        e1 = self._create_entry("a")
        e2 = self._create_entry("b")
        pairs = [(e1, e2, ComparisonOutcome(0.99, False))]

        engine._build_duplicate_groups(pairs)
        self.assertEqual(len(engine.duplicates), 1)
        group = list(engine.duplicates.values())[0]
        self.assertIn(e1, group.items)
        self.assertIn(e2, group.items)

    def test_build_duplicate_groups_two_independent_pairs(self):
        settings = CoreSettings()
        db_manager_mock = MagicMock(spec=DatabaseManager)
        engine = ScanEngine(settings, db_manager_mock)

        e_a = self._create_entry("a")
        e_b = self._create_entry("b")
        e_c = self._create_entry("c")
        e_d = self._create_entry("d")
        pairs = [
            (e_a, e_b, ComparisonOutcome(0.99, False)),
            (e_c, e_d, ComparisonOutcome(0.98, False))
        ]
        engine._build_duplicate_groups(pairs)
        self.assertEqual(len(engine.duplicates), 2)

        paths_in_groups = set()
        for group in engine.duplicates.values():
            for item in group.items:
                paths_in_groups.add(item.path)

        self.assertIn(e_a.path, paths_in_groups)
        self.assertIn(e_b.path, paths_in_groups)
        self.assertIn(e_c.path, paths_in_groups)
        self.assertIn(e_d.path, paths_in_groups)


    def test_build_duplicate_groups_chain(self):
        # A=B, B=C  => A,B,C in one group
        settings = CoreSettings()
        db_manager_mock = MagicMock(spec=DatabaseManager)
        engine = ScanEngine(settings, db_manager_mock)

        e_a = self._create_entry("a")
        e_b = self._create_entry("b")
        e_c = self._create_entry("c")
        e_d = self._create_entry("d") # unrelated

        pairs = [
            (e_a, e_b, ComparisonOutcome(0.99, False)),
            (e_b, e_c, ComparisonOutcome(0.98, False)),
            (self._create_entry("x"), self._create_entry("y"), ComparisonOutcome(0.97, False)) # separate group
        ]
        engine._build_duplicate_groups(pairs)
        self.assertEqual(len(engine.duplicates), 2, "Should be 2 groups: (A,B,C) and (X,Y)")

        found_abc_group = False
        for group in engine.duplicates.values():
            group_paths = {item.path for item in group.items}
            if e_a.path in group_paths and e_b.path in group_paths and e_c.path in group_paths:
                self.assertEqual(len(group.items), 3)
                found_abc_group = True
        self.assertTrue(found_abc_group, "Group A,B,C not found or incomplete")


    def test_build_duplicate_groups_complex_merge(self):
        # A=B, C=D, then B=C => A,B,C,D in one group
        settings = CoreSettings()
        db_manager_mock = MagicMock(spec=DatabaseManager)
        engine = ScanEngine(settings, db_manager_mock)

        e_a = self._create_entry("a")
        e_b = self._create_entry("b")
        e_c = self._create_entry("c")
        e_d = self._create_entry("d")
        e_e = self._create_entry("e") # unrelated

        pairs = [
            (e_a, e_b, ComparisonOutcome(0.99, False)), # Group1: A,B
            (e_c, e_d, ComparisonOutcome(0.98, False)), # Group2: C,D
            (e_b, e_c, ComparisonOutcome(0.97, False))  # Merge Group1 and Group2 via B,C
        ]
        engine._build_duplicate_groups(pairs)
        self.assertEqual(len(engine.duplicates), 1, "Should be 1 group: (A,B,C,D)")

        group = list(engine.duplicates.values())[0]
        self.assertEqual(len(group.items), 4)
        group_paths = {item.path for item in group.items}
        self.assertIn(e_a.path, group_paths)
        self.assertIn(e_b.path, group_paths)
        self.assertIn(e_c.path, group_paths)
        self.assertIn(e_d.path, group_paths)

    def test_build_duplicate_groups_no_actual_duplicates(self):
        # Pairs list might be empty if no pairs pass similarity threshold
        settings = CoreSettings()
        db_manager_mock = MagicMock(spec=DatabaseManager)
        engine = ScanEngine(settings, db_manager_mock)

        engine._build_duplicate_groups([]) # Simulates no pairs found by comparison phase
        self.assertEqual(len(engine.duplicates), 0)

    def test_build_duplicate_groups_item_in_multiple_pairs_forms_one_group(self):
        # A=B, A=C, A=D => A,B,C,D in one group
        settings = CoreSettings()
        db_manager_mock = MagicMock(spec=DatabaseManager)
        engine = ScanEngine(settings, db_manager_mock)

        e_a = self._create_entry("a")
        e_b = self._create_entry("b")
        e_c = self._create_entry("c")
        e_d = self._create_entry("d")

        pairs = [
            (e_a, e_b, ComparisonOutcome(0.99, False)),
            (e_a, e_c, ComparisonOutcome(0.98, False)),
            (e_a, e_d, ComparisonOutcome(0.97, False))
        ]
        engine._build_duplicate_groups(pairs)
        self.assertEqual(len(engine.duplicates), 1)
        group = list(engine.duplicates.values())[0]
        self.assertEqual(len(group.items), 4)
        group_paths = {item.path for item in group.items}
        for entry in [e_a, e_b, e_c, e_d]:
            self.assertIn(entry.path, group_paths)


class TestScanEngineGatherInfos(unittest.TestCase):
    def setUp(self):
        self.settings = CoreSettings()
        self.db_manager_mock = MagicMock(spec=DatabaseManager)
        # Mock the worker function as we are testing the selection logic of _gather_infos
        self.patcher = patch('vdf_core.scan_engine.ScanEngine._process_single_entry_info', return_value=None)
        self.mock_process_worker = self.patcher.start()
        self.engine = ScanEngine(self.settings, self.db_manager_mock)

    def tearDown(self):
        self.patcher.stop()
        FileEntry.__post_init__ = original_file_entry_post_init # Restore after each test method too

    def _create_entry_for_gather_test(
        self, name_id: str, is_image: bool = False,
        has_media_info: bool = True, duration: Optional[float] = 60.0,
        has_thumbnails: bool = True, num_thumbnails: int = 1,
        flags: EntryFlags = EntryFlags.NONE,
        thumb_sig: Optional[str] = "default_sig"
    ) -> FileEntry:
        entry = FileEntry(path=f"/test/gather_{name_id}.{'jpg' if is_image else 'mp4'}")
        entry.filename = f"gather_{name_id}.{'jpg' if is_image else 'mp4'}"
        entry.flags = flags
        if is_image: entry.flags |= EntryFlags.IS_IMAGE

        if has_media_info and not is_image:
            entry.media_info = MediaInfo(duration_seconds=duration)
        elif not is_image: # Video but no media info
             entry.media_info = None


        if has_thumbnails:
            # For videos, num_thumbnails should match settings.thumbnail_positions length
            # For images, key is 0.0
            if is_image:
                 entry.gray_bytes_data = {0.0: b"dummythumb"}
            else:
                # Simulate based on num_thumbnails matching current default setting (1 position)
                # This part needs to align with how _gather_infos checks thumbnail counts
                # For this test, let's assume settings.thumbnail_positions has 'num_thumbnails' items
                # if we are testing 'has_thumbnails=True' means it has enough.
                # A more precise test would set engine.settings.thumbnail_positions.
                if self.engine.settings.thumbnail_positions: # if positions are defined
                    for i in range(len(self.engine.settings.thumbnail_positions)):
                        if i < num_thumbnails: # only populate up to num_thumbnails
                             entry.gray_bytes_data[float(i*10)] = b"dummythumb"
                elif num_thumbnails > 0 : # if no positions but asked for thumbs (should not happen for videos)
                     entry.gray_bytes_data = {float(i*10): b"dummythumb" for i in range(num_thumbnails)}


        entry.thumb_config_signature = thumb_sig
        return entry

    def test_gather_infos_new_video_needs_processing(self):
        # New video, no media_info, no thumbnails
        new_vid = self._create_entry_for_gather_test("new_vid", has_media_info=False, has_thumbnails=False)
        self.engine._gather_infos([new_vid])
        self.mock_process_worker.assert_called_once_with(new_vid)

    def test_gather_infos_video_with_info_and_thumbs_no_retry(self):
        # Video has everything, no retry flag
        self.settings.always_retry_failed_sampling = False
        current_sig = get_thumbnail_config_signature(self.settings.thumbnail_positions)
        vid_complete = self._create_entry_for_gather_test("complete_vid", thumb_sig=current_sig)

        self.engine._gather_infos([vid_complete])
        self.mock_process_worker.assert_not_called()

    def test_gather_infos_video_retry_metadata_error(self):
        self.settings.always_retry_failed_sampling = True
        current_sig = get_thumbnail_config_signature(self.settings.thumbnail_positions)
        vid_meta_err = self._create_entry_for_gather_test(
            "meta_err_vid", has_media_info=False, flags=EntryFlags.METADATA_ERROR,
            has_thumbnails=True, thumb_sig=current_sig # Has thumbs but metadata failed
        )
        self.engine._gather_infos([vid_meta_err])
        self.mock_process_worker.assert_called_once_with(vid_meta_err)
        self.assertFalse(vid_meta_err.flags & EntryFlags.METADATA_ERROR, "Error flag should be cleared for retry")
        self.assertIsNone(vid_meta_err.media_info, "MediaInfo should be cleared for retry")


    def test_gather_infos_video_retry_thumbnail_error(self):
        self.settings.always_retry_failed_sampling = True
        current_sig = get_thumbnail_config_signature(self.settings.thumbnail_positions)
        vid_thumb_err = self._create_entry_for_gather_test(
            "thumb_err_vid", flags=EntryFlags.THUMBNAIL_ERROR,
            has_thumbnails=False, # No actual thumbnails due to error
            thumb_sig=current_sig
        )
        self.engine._gather_infos([vid_thumb_err])
        self.mock_process_worker.assert_called_once_with(vid_thumb_err)
        self.assertFalse(vid_thumb_err.flags & EntryFlags.THUMBNAIL_ERROR, "Error flag should be cleared")
        self.assertEqual(len(vid_thumb_err.gray_bytes_data), 0, "Thumbnails should be cleared")

    def test_gather_infos_video_thumb_config_mismatch(self):
        self.settings.always_retry_failed_sampling = False # Retry not forced by error
        old_sig = "old_sig_123"
        vid_sig_mismatch = self._create_entry_for_gather_test("sig_mismatch_vid", thumb_sig=old_sig)

        # Ensure current settings produce a different signature
        self.assertNotEqual(get_thumbnail_config_signature(self.settings.thumbnail_positions), old_sig)

        self.engine._gather_infos([vid_sig_mismatch])
        self.mock_process_worker.assert_called_once_with(vid_sig_mismatch)
        self.assertEqual(len(vid_sig_mismatch.gray_bytes_data), 0, "Thumbnails should be cleared on sig mismatch")

    def test_gather_infos_image_needs_processing_if_no_thumb(self):
        img_no_thumb = self._create_entry_for_gather_test("img_no_thumb", is_image=True, has_thumbnails=False)
        self.engine._gather_infos([img_no_thumb])
        self.mock_process_worker.assert_called_once_with(img_no_thumb)

    def test_gather_infos_image_complete_no_retry(self):
        self.settings.always_retry_failed_sampling = False
        img_complete = self._create_entry_for_gather_test("img_complete", is_image=True, has_thumbnails=True)
        # Image thumb_sig is not checked against settings' positions, but against having a 0.0 key mostly
        self.engine._gather_infos([img_complete])
        self.mock_process_worker.assert_not_called()

    def test_gather_infos_image_retry_thumb_error(self):
        self.settings.always_retry_failed_sampling = True
        img_thumb_err = self._create_entry_for_gather_test(
            "img_thumb_err", is_image=True, flags=EntryFlags.THUMBNAIL_ERROR, has_thumbnails=False
        )
        self.engine._gather_infos([img_thumb_err])
        self.mock_process_worker.assert_called_once_with(img_thumb_err)
        self.assertFalse(img_thumb_err.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertEqual(len(img_thumb_err.gray_bytes_data),0)


    def test_gather_infos_video_not_enough_thumbnails_for_config(self):
        # Settings expect 2 thumbnails, entry only has 1
        self.settings.thumbnail_positions = [
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 10.0),
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 90.0)
        ]
        current_sig = get_thumbnail_config_signature(self.settings.thumbnail_positions)
        vid_not_enough = self._create_entry_for_gather_test(
            "vid_not_enough",
            num_thumbnails=1, # Has only one thumbnail
            thumb_sig=current_sig # Signature matches, but count is low
        )
        # Manually ensure only one thumbnail for the test setup based on num_thumbnails
        vid_not_enough.gray_bytes_data = {10.0: b"one"}


        self.engine._gather_infos([vid_not_enough])
        self.mock_process_worker.assert_called_once_with(vid_not_enough)


@patch('vdf_core.scan_engine.os.path.exists')
@patch('vdf_core.scan_engine.os.stat')
@patch('vdf_core.scan_engine.os.walk')
class TestScanEngineBuildFileList(unittest.TestCase):

    def setUp(self):
        self.settings = CoreSettings()
        self.db_manager_mock = MagicMock(spec=DatabaseManager)
        self.engine = ScanEngine(self.settings, self.db_manager_mock)
        # Mock methods of db_manager that will be called by _build_file_list
        self.db_manager_mock.get_entry.return_value = None
        self.db_manager_mock.add_or_update_entry.return_value = None
        self.db_manager_mock.remove_entry.return_value = None
        self.db_manager_mock.get_all_paths.return_value = set()

        # Mock FileEntry's __post_init__ to control its behavior during tests
        # This is already globally mocked, but ensure it's the test one for clarity
        FileEntry.__post_init__ = mock_file_entry_post_init_for_scan_engine_tests


    def tearDown(self):
        FileEntry.__post_init__ = original_file_entry_post_init # Restore original

    def _create_mock_stat_result(self, size, mtime, ctime=None):
        stat_res = MagicMock()
        stat_res.st_size = size
        stat_res.st_mtime = mtime
        stat_res.st_ctime = ctime if ctime is not None else mtime
        return stat_res

    def test_build_file_list_empty_dir(self, mock_walk, mock_stat, mock_os_path_exists):
        self.settings.include_list = {"/scan/empty_dir"}
        mock_walk.return_value = iter([("/scan/empty_dir", [], [])]) # Single top dir, no subdirs, no files
        mock_os_path_exists.return_value = True # Assume scan dir exists

        self.engine._build_file_list()

        mock_walk.assert_called_once_with(os.path.abspath("/scan/empty_dir"), topdown=True, followlinks=not self.settings.ignore_reparse_points)
        self.db_manager_mock.add_or_update_entry.assert_not_called()
        self.assertEqual(self.engine._current_phase_max_value, 0) # Max files to process

    def test_build_file_list_finds_new_media_file(self, mock_walk, mock_stat, mock_os_path_exists):
        scan_path = os.path.abspath("/scan/dir_with_new")
        self.settings.include_list = {scan_path}

        file_name = "video.mp4"
        file_abs_path = os.path.join(scan_path, file_name)

        mock_walk.return_value = iter([(scan_path, [], [file_name])])
        mock_os_path_exists.return_value = True # For scan_path and file_path
        mock_stat.return_value = self._create_mock_stat_result(size=1024, mtime=1234567890)
        self.db_manager_mock.get_entry.return_value = None # File not in DB

        self.engine._build_file_list()

        self.db_manager_mock.get_entry.assert_called_with(file_abs_path)
        # Check that add_or_update_entry was called with a FileEntry for file_abs_path
        self.db_manager_mock.add_or_update_entry.assert_called_once()
        added_entry_arg = self.db_manager_mock.add_or_update_entry.call_args[0][0]
        self.assertIsInstance(added_entry_arg, FileEntry)
        self.assertEqual(added_entry_arg.path, file_abs_path)
        self.assertEqual(self.engine._current_phase_max_value, 1)


    def test_build_file_list_ignores_non_media_extension(self, mock_walk, mock_stat, mock_os_path_exists):
        scan_path = os.path.abspath("/scan/dir_mixed")
        self.settings.include_list = {scan_path}
        mock_walk.return_value = iter([(scan_path, [], ["video.mp4", "text.txt"])])
        mock_os_path_exists.return_value = True
        mock_stat.return_value = self._create_mock_stat_result(size=1024, mtime=1234567890)
        self.db_manager_mock.get_entry.return_value = None

        self.engine._build_file_list()
        # add_or_update_entry should only be called for "video.mp4"
        self.db_manager_mock.add_or_update_entry.assert_called_once()
        added_entry_arg = self.db_manager_mock.add_or_update_entry.call_args[0][0]
        self.assertEqual(added_entry_arg.filename, "video.mp4")
        self.assertEqual(self.engine._current_phase_max_value, 1) # Only 1 media file

    def test_build_file_list_existing_unchanged_file(self, mock_walk, mock_stat, mock_os_path_exists):
        scan_path = os.path.abspath("/scan/dir_existing")
        self.settings.include_list = {scan_path}
        file_name = "existing.mkv"
        file_abs_path = os.path.join(scan_path, file_name)

        mock_walk.return_value = iter([(scan_path, [], [file_name])])
        mock_os_path_exists.return_value = True

        mtime = 1234567890.0
        size = 2048
        mock_stat.return_value = self._create_mock_stat_result(size=size, mtime=mtime)

        # Mock FileEntry that would be returned by db_manager.get_entry
        db_entry_mock = FileEntry(path=file_abs_path) # mock post_init used
        db_entry_mock.path = file_abs_path # ensure path is set
        db_entry_mock.date_modified_utc = datetime.utcfromtimestamp(mtime)
        db_entry_mock.file_size_bytes = size
        self.db_manager_mock.get_entry.return_value = db_entry_mock

        self.engine._build_file_list()

        self.db_manager_mock.get_entry.assert_called_with(file_abs_path)
        self.db_manager_mock.add_or_update_entry.assert_not_called() # Not called if unchanged
        self.assertEqual(self.engine._current_phase_max_value, 1) # Counted, but not "processed" for adding

    def test_build_file_list_existing_modified_file(self, mock_walk, mock_stat, mock_os_path_exists):
        scan_path = os.path.abspath("/scan/dir_modified")
        self.settings.include_list = {scan_path}
        file_name = "modified.avi"
        file_abs_path = os.path.join(scan_path, file_name)

        mock_walk.return_value = iter([(scan_path, [], [file_name])])
        mock_os_path_exists.return_value = True

        # DB entry has old stat info
        db_entry_mock = FileEntry(path=file_abs_path)
        db_entry_mock.path = file_abs_path
        db_entry_mock.date_modified_utc = datetime.utcfromtimestamp(1000000000.0) # Old mtime
        db_entry_mock.file_size_bytes = 1000 # Old size
        db_entry_mock.media_info = MediaInfo(duration_seconds=10) # Has old metadata
        db_entry_mock.gray_bytes_data = {0.0: b"old_thumb"}   # Has old thumbs
        self.db_manager_mock.get_entry.return_value = db_entry_mock

        # os.stat returns new info
        new_mtime = 1234567890.0
        new_size = 2000
        mock_stat.return_value = self._create_mock_stat_result(size=new_size, mtime=new_mtime)

        self.engine._build_file_list()

        self.db_manager_mock.get_entry.assert_called_with(file_abs_path)
        self.db_manager_mock.add_or_update_entry.assert_called_once()
        updated_entry_arg = self.db_manager_mock.add_or_update_entry.call_args[0][0]

        self.assertEqual(updated_entry_arg.path, file_abs_path)
        self.assertIsNone(updated_entry_arg.media_info, "MediaInfo should be cleared for modified file")
        self.assertEqual(len(updated_entry_arg.gray_bytes_data), 0, "Thumbnails should be cleared")
        self.assertEqual(updated_entry_arg.file_size_bytes, new_size)
        self.assertEqual(updated_entry_arg.date_modified_utc.timestamp(), new_mtime)
        self.assertEqual(self.engine._current_phase_max_value, 1)

    def test_build_file_list_no_subdirectories(self, mock_walk, mock_stat, mock_os_path_exists):
        self.settings.include_subdirectories = False
        scan_path = os.path.abspath("/scan/top_level")
        sub_dir_path = os.path.join(scan_path, "subdir")
        self.settings.include_list = {scan_path}

        # os.walk will be called once for scan_path.
        # It should then be called for subdir, but dirs[:] will be modified.
        mock_walk_iter = iter([
            (scan_path, ["subdir"], ["file_top.mp4"]),  # Yields top level
            (sub_dir_path, [], ["file_sub.mp4"])        # This should effectively be skipped by logic
        ])
        mock_walk.side_effect = lambda path, topdown, followlinks: mock_walk_iter if path == scan_path else iter([])


        mock_os_path_exists.return_value = True
        mock_stat.return_value = self._create_mock_stat_result(size=100, mtime=100)
        self.db_manager_mock.get_entry.return_value = None

        self.engine._build_file_list()

        # add_or_update_entry should only be called for "file_top.mp4"
        self.db_manager_mock.add_or_update_entry.assert_called_once()
        added_entry_arg = self.db_manager_mock.add_or_update_entry.call_args[0][0]
        self.assertEqual(added_entry_arg.filename, "file_top.mp4")
        self.assertEqual(self.engine._current_phase_max_value, 1) # Only 1 file processed


    def test_build_file_list_removes_non_existent_if_setting_is_false(self, mock_walk, mock_stat, mock_os_path_exists):
        self.settings.include_non_existing_files = False
        scan_path = os.path.abspath("/scan/some_files")
        self.settings.include_list = {scan_path}

        # Files found by os.walk (these "exist" on disk for the scan)
        file1_on_disk = "file1.mp4"
        file1_abs_path = os.path.join(scan_path, file1_on_disk)
        mock_walk.return_value = iter([(scan_path, [], [file1_on_disk])])

        # Mock os.stat for file1_on_disk
        mock_stat.side_effect = lambda path: self._create_mock_stat_result(1024, 123) if path == file1_abs_path else None

        # DB has file1 and file2. file2 is no longer on disk.
        db_entry1_mock = FileEntry(path=file1_abs_path); db_entry1_mock.path = file1_abs_path
        db_entry2_mock_path = os.path.abspath(os.path.join(scan_path, "file2_gone.mkv"))

        # Simulate get_entry returning None for new files, and the mock for existing
        def mock_get_entry(path_arg):
            if path_arg == file1_abs_path: return db_entry1_mock # Unchanged
            return None
        self.db_manager_mock.get_entry.side_effect = mock_get_entry
        self.db_manager_mock.get_all_paths.return_value = {file1_abs_path, db_entry2_mock_path}

        mock_os_path_exists.return_value = True # For scan dir

        self.engine._build_file_list()

        # file1 should not be re-added if unchanged (assuming mtime/size match, which it does here implicitly
        # because we return the same db_entry1_mock and its mtime/size won't be compared against a new stat).
        # For this test, let's assume file1 was unchanged, so add_or_update_entry is not called for it.
        # The key is that file2_gone.mkv should be removed.

        # Let's refine the mock for get_entry to simulate file1 as new for clarity of this test
        # or better, ensure db_entry1_mock's stats match what mock_stat would return for file1_abs_path
        db_entry1_mock.date_modified_utc = datetime.utcfromtimestamp(123)
        db_entry1_mock.file_size_bytes = 1024

        # Re-run with refined mocks for file1 being "unchanged"
        self.engine._build_file_list()

        self.db_manager_mock.remove_entry.assert_called_once_with(db_entry2_mock_path)
        # add_or_update_entry should not have been called for file1 if it was truly unchanged.
        # If it was considered "new" because get_entry returned None initially, it would be called.
        # The test for "unchanged" already covers no add_or_update.
        # Here we primarily test the remove_entry call.


# TODO: Add tests for ScanEngine.start_scan workflow (integration style with many mocks)


if __name__ == '__main__':
    # Restore original FileEntry.__post_init__ if running this file directly for some reason,
    # though usually tests are run via unittest discover.
    # FileEntry.__post_init__ = original_file_entry_post_init
    unittest.main()


@patch('vdf_core.scan_engine.get_media_info')
@patch('vdf_core.scan_engine.extract_gray_bytes_for_file_entry')
class TestScanEngineProcessSingleEntryInfo(unittest.TestCase):
    def setUp(self):
        self.settings = CoreSettings()
        self.db_manager_mock = MagicMock(spec=DatabaseManager) # Not directly used by _process_single_entry_info
        self.engine = ScanEngine(self.settings, self.db_manager_mock)
        FileEntry.__post_init__ = mock_file_entry_post_init_for_scan_engine_tests

    def tearDown(self):
        FileEntry.__post_init__ = original_file_entry_post_init

    def _create_base_entry(self, path_suffix: str, is_image: bool = False) -> FileEntry:
        entry = FileEntry(path=f"/test/process_{path_suffix}.{'jpg' if is_image else 'mp4'}")
        entry.filename = f"process_{path_suffix}.{'jpg' if is_image else 'mp4'}"
        if is_image: entry.flags |= EntryFlags.IS_IMAGE
        return entry

    def test_process_video_success(self, mock_extract_thumbs, mock_get_meta):
        entry = self._create_base_entry("vid_ok")

        mock_get_meta.return_value = MediaInfo(duration_seconds=60.0)
        mock_extract_thumbs.return_value = True # Indicates overall success from util
        # Simulate that extract_gray_bytes_for_file_entry populates gray_bytes_data
        def side_effect_extract_thumbs(e, s):
            e.gray_bytes_data = {0.0: b"thumbdata"} # Simulate some data was added
            return True
        mock_extract_thumbs.side_effect = side_effect_extract_thumbs

        self.engine._process_single_entry_info(entry)

        mock_get_meta.assert_called_once_with(entry.path, self.settings)
        mock_extract_thumbs.assert_called_once_with(entry, self.settings)
        self.assertIsNotNone(entry.media_info)
        self.assertTrue(entry.gray_bytes_data)
        self.assertFalse(entry.flags & EntryFlags.METADATA_ERROR)
        self.assertFalse(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertFalse(entry.is_invalid_for_scan)
        self.assertIsNotNone(entry.thumb_config_signature) # Should be set on success

    def test_process_video_metadata_fails(self, mock_extract_thumbs, mock_get_meta):
        entry = self._create_base_entry("vid_meta_fail")
        mock_get_meta.return_value = None # Simulate metadata failure

        self.engine._process_single_entry_info(entry)

        mock_get_meta.assert_called_once_with(entry.path, self.settings)
        mock_extract_thumbs.assert_not_called() # Should not attempt thumbs if metadata fails
        self.assertTrue(entry.flags & EntryFlags.METADATA_ERROR)
        self.assertTrue(entry.is_invalid_for_scan)

    def test_process_video_thumbnail_extraction_fails_critically(self, mock_extract_thumbs, mock_get_meta):
        entry = self._create_base_entry("vid_thumb_crit_fail")
        entry.media_info = MediaInfo(duration_seconds=30.0) # Metadata is fine

        mock_get_meta.return_value = entry.media_info # Already has it
        mock_extract_thumbs.return_value = False # Util reports critical failure

        self.engine._process_single_entry_info(entry)

        # get_media_info might not be called if entry already has it and no retry flag
        # For this test, assume it's called or not based on initial state (no retry flags set)
        # mock_get_meta.assert_called_once() # Or assert_not_called if pre-populated and no retry
        mock_extract_thumbs.assert_called_once_with(entry, self.settings)

        # extract_gray_bytes_for_file_entry itself sets THUMBNAIL_ERROR if it returns False
        # and no thumbnails are produced.
        # Here we assume our mock means no thumbnails were added.
        self.assertFalse(entry.gray_bytes_data)
        self.assertTrue(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertTrue(entry.is_invalid_for_scan)


    def test_process_video_thumbnail_extraction_partial_fail_sets_flag(self, mock_extract_thumbs, mock_get_meta):
        entry = self._create_base_entry("vid_thumb_part_fail")
        entry.media_info = MediaInfo(duration_seconds=30.0)

        mock_get_meta.return_value = entry.media_info
        # Simulate extract_gray_bytes_for_file_entry succeeded overall but set a flag internally
        # because some thumbnails failed, but some succeeded.
        def side_effect_extract_thumbs_partial(e, s):
            e.gray_bytes_data = {0.0: b"partial_thumb"} # some data
            e.flags |= EntryFlags.THUMBNAIL_ERROR # Flag set due to partial failure
            return True # Overall call to util is true
        mock_extract_thumbs.side_effect = side_effect_extract_thumbs_partial

        self.engine._process_single_entry_info(entry)

        self.assertTrue(entry.gray_bytes_data)
        self.assertTrue(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        # is_invalid_for_scan depends on whether THUMBNAIL_ERROR + no actual data makes it invalid.
        # Current logic: THUMBNAIL_ERROR + no gray_bytes_data -> invalid.
        # Here, it has data, so it should NOT be invalid_for_scan solely due to this partial error.
        self.assertFalse(entry.is_invalid_for_scan)
        self.assertIsNotNone(entry.thumb_config_signature)


    def test_process_image_success(self, mock_extract_thumbs, mock_get_meta):
        entry = self._create_base_entry("img_ok", is_image=True)

        def side_effect_extract_thumbs_img(e, s):
            e.gray_bytes_data = {0.0: b"img_thumb"}
            return True
        mock_extract_thumbs.side_effect = side_effect_extract_thumbs_img

        self.engine._process_single_entry_info(entry)

        mock_get_meta.assert_not_called() # No metadata for images
        mock_extract_thumbs.assert_called_once_with(entry, self.settings)
        self.assertTrue(entry.gray_bytes_data)
        self.assertFalse(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertFalse(entry.is_invalid_for_scan)
        self.assertEqual(entry.thumb_config_signature, get_thumbnail_config_signature([]))


    def test_process_image_thumb_extraction_fails(self, mock_extract_thumbs, mock_get_meta):
        entry = self._create_base_entry("img_fail", is_image=True)
        mock_extract_thumbs.return_value = False # Util reports critical failure

        self.engine._process_single_entry_info(entry)

        mock_extract_thumbs.assert_called_once_with(entry, self.settings)
        self.assertFalse(entry.gray_bytes_data)
        self.assertTrue(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertTrue(entry.is_invalid_for_scan)
        self.assertIsNone(entry.thumb_config_signature) # Not set on failure

    def test_process_video_retry_logic(self, mock_extract_thumbs, mock_get_meta):
        self.settings.always_retry_failed_sampling = True
        entry = self._create_base_entry("vid_retry")
        entry.flags = EntryFlags.METADATA_ERROR | EntryFlags.THUMBNAIL_ERROR # Has previous errors
        entry.media_info = None # Was None due to METADATA_ERROR
        entry.gray_bytes_data = {} # Was empty due to THUMBNAIL_ERROR

        # _gather_infos would have cleared these flags before calling _process_single_entry_info
        # So, _process_single_entry_info receives an entry with flags already cleared for retry.
        # Let's simulate that state here for _process_single_entry_info's perspective.
        entry.flags = EntryFlags.NONE

        mock_get_meta.return_value = MediaInfo(duration_seconds=10.0)
        def side_effect_extract_thumbs_retry(e,s):
            e.gray_bytes_data = {5.0: b"new_thumb"}
            return True
        mock_extract_thumbs.side_effect = side_effect_extract_thumbs_retry

        self.engine._process_single_entry_info(entry)

        mock_get_meta.assert_called_once()
        mock_extract_thumbs.assert_called_once()
        self.assertFalse(entry.flags & EntryFlags.METADATA_ERROR)
        self.assertFalse(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertTrue(entry.gray_bytes_data)
        self.assertIsNotNone(entry.thumb_config_signature)

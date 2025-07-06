import unittest
import os
from typing import Dict, Optional

# Ensure vdf_core is in path for testing
import sys
project_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root_dir)

from vdf_core.file_entry import FileEntry, MediaInfo, EntryFlags
from vdf_core.settings import CoreSettings, ThumbnailPositionSetting, ThumbnailPositionType
from vdf_core.comparison import check_if_entries_are_duplicate, _calculate_expected_gray_bytes_key
from vdf_core.image_utils import GRAY_BYTE_LENGTH # For creating mock thumbnail data

# Mock __post_init__ for FileEntry to avoid file system access during these tests
original_post_init = FileEntry.__post_init__
def mock_file_entry_post_init(self_fe):
    # Minimal post_init for testing comparison logic
    if not hasattr(self_fe, 'path') or not self_fe.path: self_fe.path = "dummy/path.vid"
    self_fe.path = os.path.abspath(self_fe.path)
    # IS_IMAGE flag might be needed by comparison logic if it branches
    if self_fe.path.lower().endswith((".jpg", ".png")): # Example image extensions
        self_fe.flags |= EntryFlags.IS_IMAGE
    if not hasattr(self_fe, 'flags'): self_fe.flags = EntryFlags.NONE
    if not hasattr(self_fe, 'gray_bytes_data'): self_fe.gray_bytes_data = {}
    # Other fields like file_size_bytes, date_modified_utc are not directly used by
    # check_if_entries_are_duplicate but are by ScanEngine filtering prior to comparison.

FileEntry.__post_init__ = mock_file_entry_post_init


class TestComparison(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        # Restore original __post_init__ after all tests in this class are done
        FileEntry.__post_init__ = original_post_init

    def _create_mock_entry(
        self,
        path: str,
        is_image: bool = False,
        duration: Optional[float] = None,
        gray_bytes: Optional[Dict[float, Optional[bytes]]] = None,
        flags: EntryFlags = EntryFlags.NONE
    ) -> FileEntry:
        entry = FileEntry(path=path) # __post_init__ is mocked
        entry.path = os.path.abspath(path) # Ensure path is set after mock
        entry.flags = flags
        if is_image:
            entry.flags |= EntryFlags.IS_IMAGE

        if duration is not None:
            entry.media_info = MediaInfo(duration_seconds=duration)

        entry.gray_bytes_data = gray_bytes if gray_bytes is not None else {}
        return entry

    def test_calculate_expected_gray_bytes_key(self):
        pos_perc = ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)
        pos_start = ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, 10.0)
        pos_end = ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_END, 5.0)

        self.assertAlmostEqual(_calculate_expected_gray_bytes_key(pos_perc, 100.0), 50.0)
        self.assertAlmostEqual(_calculate_expected_gray_bytes_key(pos_perc, 0.0), 0.0) # Duration 0

        self.assertAlmostEqual(_calculate_expected_gray_bytes_key(pos_start, 100.0), 10.0)
        self.assertAlmostEqual(_calculate_expected_gray_bytes_key(pos_start, 5.0), 5.0) # Offset >= duration, clamped

        self.assertAlmostEqual(_calculate_expected_gray_bytes_key(pos_end, 100.0), 95.0)
        self.assertAlmostEqual(_calculate_expected_gray_bytes_key(pos_end, 3.0), 0.0) # Offset > duration, clamped to 0

    def test_check_if_entries_are_duplicate_image_vs_video(self):
        img_entry = self._create_mock_entry("test.jpg", is_image=True)
        vid_entry = self._create_mock_entry("test.mp4", duration=10.0)
        settings = CoreSettings()
        self.assertIsNone(check_if_entries_are_duplicate(img_entry, vid_entry, settings))

    def test_check_if_entries_are_duplicate_video_duration_mismatch(self):
        vid1 = self._create_mock_entry("vid1.mp4", duration=100.0)
        vid2 = self._create_mock_entry("vid2.mp4", duration=10.0) # More than 20% diff
        settings = CoreSettings(duration_difference_percent=20.0)
        self.assertIsNone(check_if_entries_are_duplicate(vid1, vid2, settings))

        vid3 = self._create_mock_entry("vid3.mp4", duration=85.0) # Within 20% of 100s
        # Need thumbnails for them to be compared further
        thumb_data = b'\xAB' * GRAY_BYTE_LENGTH
        vid1.gray_bytes_data = {50.0: thumb_data} # Assuming key matches calculation for 50%
        vid3.gray_bytes_data = {42.5: thumb_data} # Assuming key matches calculation for 50%

        settings.thumbnail_positions = [ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)]
        # If they have identical thumbnails, they should match
        outcome = check_if_entries_are_duplicate(vid1, vid3, settings)
        self.assertIsNotNone(outcome)
        if outcome: self.assertAlmostEqual(outcome[0], 1.0) # Similarity

    def test_check_if_entries_are_duplicate_identical_images(self):
        thumb_data = b'\x10' * GRAY_BYTE_LENGTH
        img1 = self._create_mock_entry("img1.jpg", is_image=True, gray_bytes={0.0: thumb_data})
        img2 = self._create_mock_entry("img2.jpg", is_image=True, gray_bytes={0.0: thumb_data})
        settings = CoreSettings()

        outcome = check_if_entries_are_duplicate(img1, img2, settings)
        self.assertIsNotNone(outcome)
        if outcome:
            self.assertAlmostEqual(outcome[0], 1.0) # Similarity
            self.assertFalse(outcome[1]) # Not flipped

    def test_check_if_entries_are_duplicate_different_images(self):
        img1 = self._create_mock_entry("img1_diff.jpg", is_image=True, gray_bytes={0.0: b'\x00' * GRAY_BYTE_LENGTH})
        img2 = self._create_mock_entry("img2_diff.jpg", is_image=True, gray_bytes={0.0: b'\xFF' * GRAY_BYTE_LENGTH})
        settings = CoreSettings(similarity_threshold_percent=90.0) # Difference limit 0.1

        outcome = check_if_entries_are_duplicate(img1, img2, settings)
        self.assertIsNone(outcome) # Should be too different (diff = 1.0)

    def test_check_if_entries_are_duplicate_image_flipped_match(self):
        # Create two images where one is a flipped version of the other
        original_row = bytes(list(range(8)) + list(range(8))) # Not symmetric
        flipped_row = bytes(list(range(7,-1,-1)) + list(range(7,-1,-1)))

        thumb_original = b''.join([original_row]*16) # 16 * 16 = 256
        thumb_flipped = b''.join([flipped_row]*16)

        img1 = self._create_mock_entry("img_orig.png", is_image=True, gray_bytes={0.0: thumb_original})
        img2 = self._create_mock_entry("img_flip_of_orig.png", is_image=True, gray_bytes={0.0: thumb_flipped})

        settings_no_flip = CoreSettings(compare_horizontally_flipped=False, similarity_threshold_percent=99.0)
        settings_with_flip = CoreSettings(compare_horizontally_flipped=True, similarity_threshold_percent=99.0)

        # Without flip comparison, they should not match (assuming they are different enough)
        # Note: The mock flip_gray_bytes in image_utils would be used.
        # Here, we are providing an already flipped version to img2, and img1's internal flip will be compared.
        # So, if img1_original vs img2_flipped is a poor match,
        # but img1_flipped vs img2_flipped is a good match, then it's a flipped match.
        # Let's make img2 be the original, and img1 be the one that needs flipping to match.
        # So img1 has thumb_flipped, img2 has thumb_original.
        # img1 vs img2 -> thumb_flipped vs thumb_original (no match expected)
        # img1_flipped vs img2 -> (thumb_flipped)_flipped vs thumb_original => thumb_original vs thumb_original (match!)

        img1_for_flip = self._create_mock_entry("img_needs_flip.png", is_image=True, gray_bytes={0.0: thumb_flipped})
        img2_reference = self._create_mock_entry("img_reference.png", is_image=True, gray_bytes={0.0: thumb_original})

        # This will be very different, >1% diff
        self.assertIsNone(check_if_entries_are_duplicate(img1_for_flip, img2_reference, settings_no_flip))

        outcome_flipped = check_if_entries_are_duplicate(img1_for_flip, img2_reference, settings_with_flip)
        self.assertIsNotNone(outcome_flipped, "Should find a match when flipping is enabled")
        if outcome_flipped:
            self.assertAlmostEqual(outcome_flipped[0], 1.0, msg="Similarity should be near 1.0 for flipped match")
            self.assertTrue(outcome_flipped[1], "Match should be flagged as flipped")


    def test_check_if_entries_are_duplicate_videos_identical_thumbnails(self):
        settings = CoreSettings(
            thumbnail_positions=[ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)],
            similarity_threshold_percent=99.0
        )
        thumb_data = b'\xAB' * GRAY_BYTE_LENGTH

        # Keys are calculated by _calculate_expected_gray_bytes_key
        # For duration 100.0, 50% key is 50.0
        # For duration 200.0, 50% key is 100.0
        vid1 = self._create_mock_entry("vid1_match.mp4", duration=100.0, gray_bytes={50.0: thumb_data})
        vid2 = self._create_mock_entry("vid2_match.mp4", duration=100.0, gray_bytes={50.0: thumb_data}) # Same duration, same key

        outcome = check_if_entries_are_duplicate(vid1, vid2, settings)
        self.assertIsNotNone(outcome)
        if outcome: self.assertAlmostEqual(outcome[0], 1.0)

        vid3 = self._create_mock_entry("vid3_match_dur.mp4", duration=110.0, gray_bytes={55.0: thumb_data}) # Diff duration, key 55.0
        outcome_dur = check_if_entries_are_duplicate(vid1, vid3, settings) # Durations are within 20%
        self.assertIsNotNone(outcome_dur)
        if outcome_dur: self.assertAlmostEqual(outcome_dur[0], 1.0)

    def test_check_if_entries_are_duplicate_video_duration_at_limits(self):
        vid_base = self._create_mock_entry("vid_base.mp4", duration=100.0, gray_bytes={50.0: b'\xAA'*GRAY_BYTE_LENGTH})
        settings = CoreSettings(
            duration_difference_percent=20.0, # Max ratio 1.2, Min ratio 0.8
            similarity_threshold_percent=99.0,
            thumbnail_positions=[ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)]
        )

        # Duration = 120.0 (100.0 * 1.20). Ratio vid_base/vid_test = 100/120 = 0.833 (ok)
        # Ratio vid_test/vid_base = 120/100 = 1.20 (ok)
        vid_test_upper = self._create_mock_entry("vid_upper.mp4", duration=120.0, gray_bytes={60.0: b'\xAA'*GRAY_BYTE_LENGTH})
        outcome_upper = check_if_entries_are_duplicate(vid_base, vid_test_upper, settings)
        self.assertIsNotNone(outcome_upper, "Duration 100s vs 120s should be within 20% tolerance")

        # Duration = 80.0 (100.0 * 0.80). Ratio vid_base/vid_test = 100/80 = 1.25 (too high for vid_test as denominator)
        # Ratio vid_test/vid_base = 80/100 = 0.80 (ok)
        # The C# logic is `p = entry.duration / compItem.duration * 100`. So, 100/80 = 125. Max is 120. This should fail.
        # Let's re-check the C# logic: `p > maxPercentDurationDifference || p < minPercentDurationDifference`
        # maxPercent = 100 + 20 = 120. minPercent = 100 - 20 = 80.
        # If entry1=100, entry2=80. p = 100/80 * 100 = 125.  125 > 120, so fails. Correct.
        vid_test_lower = self._create_mock_entry("vid_lower.mp4", duration=80.0, gray_bytes={40.0: b'\xAA'*GRAY_BYTE_LENGTH})
        outcome_lower = check_if_entries_are_duplicate(vid_base, vid_test_lower, settings)
        self.assertIsNone(outcome_lower, "Duration 100s vs 80s should be outside 20% tolerance (100/80 = 1.25)")

        # Test exact boundary for ratio: 100s vs 83.333s (ratio 1.2) or 100s vs 125s (ratio 0.8)
        # If base=100, comp=125. ratio = 100/125 * 100 = 80. This is exactly min_allowed_ratio. Should pass.
        vid_test_min_pass = self._create_mock_entry("vid_min_pass.mp4", duration=125.0, gray_bytes={62.5: b'\xAA'*GRAY_BYTE_LENGTH})
        outcome_min_pass = check_if_entries_are_duplicate(vid_base, vid_test_min_pass, settings)
        self.assertIsNotNone(outcome_min_pass, "100s vs 125s (ratio 0.8) should pass")

        # If base=100, comp=126. ratio = 100/126 * 100 = 79.36. This is < min_allowed_ratio. Should fail.
        vid_test_min_fail = self._create_mock_entry("vid_min_fail.mp4", duration=126.0, gray_bytes={63.0: b'\xAA'*GRAY_BYTE_LENGTH})
        outcome_min_fail = check_if_entries_are_duplicate(vid_base, vid_test_min_fail, settings)
        self.assertIsNone(outcome_min_fail, "100s vs 126s (ratio 0.7936) should fail")

        # If base=100, comp=83.333 (approx for 100/X * 100 = 120 => X = 100/1.2 = 83.333)
        # Ratio = 100/83.333 * 100 = 120. This is exactly max_allowed_ratio. Should pass.
        vid_test_max_pass = self._create_mock_entry("vid_max_pass.mp4", duration=100.0/1.2, gray_bytes={ (100.0/1.2)*0.5 : b'\xAA'*GRAY_BYTE_LENGTH})
        outcome_max_pass = check_if_entries_are_duplicate(vid_base, vid_test_max_pass, settings)
        self.assertIsNotNone(outcome_max_pass, "100s vs ~83.33s (ratio 1.2) should pass")

        # If base=100, comp=83.0 (ratio 100/83 * 100 = 120.48). This is > max_allowed_ratio. Should fail.
        vid_test_max_fail = self._create_mock_entry("vid_max_fail.mp4", duration=83.0, gray_bytes={41.5: b'\xAA'*GRAY_BYTE_LENGTH})
        outcome_max_fail = check_if_entries_are_duplicate(vid_base, vid_test_max_fail, settings)
        self.assertIsNone(outcome_max_fail, "100s vs 83s (ratio 1.2048) should fail")


    def test_check_if_entries_are_duplicate_videos_one_thumbnail_differs(self):
        settings = CoreSettings(
            thumbnail_positions=[
                ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 10.0),
                ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 90.0)
            ],
            similarity_threshold_percent=95.0 # Diff limit 0.05
        )
        thumb_A = b'\xAA' * GRAY_BYTE_LENGTH
        thumb_B = b'\xBB' * GRAY_BYTE_LENGTH # Difference will be (0xBB-0xAA)/255 = 17/255 approx 0.066
        thumb_C = b'\xAD' * GRAY_BYTE_LENGTH # Difference (0xAD-0xAA)/255 = 3/255 approx 0.011 (within limit)

        # vid1: {10.0: A, 90.0: A}
        # vid2: {10.0: C, 90.0: B} -> 90.0 thumbnails differ too much
        vid1 = self._create_mock_entry("vid_multi1.mp4", duration=100.0, gray_bytes={10.0: thumb_A, 90.0: thumb_A})
        vid2 = self._create_mock_entry("vid_multi2.mp4", duration=100.0, gray_bytes={10.0: thumb_C, 90.0: thumb_B})

        self.assertIsNone(check_if_entries_are_duplicate(vid1, vid2, settings))

        # vid3: {10.0: C, 90.0: C} -> All match within limits
        vid3 = self._create_mock_entry("vid_multi3.mp4", duration=100.0, gray_bytes={10.0: thumb_C, 90.0: thumb_C})
        outcome = check_if_entries_are_duplicate(vid1, vid3, settings)
        self.assertIsNotNone(outcome)
        if outcome:
            # Expected diff for (A,C) is 3/255. Avg diff = (3/255 + 3/255)/2 = 3/255.
            # Similarity = 1 - (3/255)
            expected_similarity = 1.0 - (3.0 / 255.0)
            self.assertAlmostEqual(outcome[0], expected_similarity, places=5)


    def test_check_if_entries_are_duplicate_similarity_threshold_edge_cases(self):
        settings = CoreSettings(
            thumbnail_positions=[ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)],
            similarity_threshold_percent=98.0 # Difference limit = 0.02
        )
        # Difference for (A,C) is 3/255 approx 0.01176
        # Difference for (A,B) where B is A+4 is 4/255 approx 0.01568
        # Difference for (A,D) where D is A+5 is 5/255 approx 0.01960 (just within 0.02 limit)
        # Difference for (A,E) where E is A+6 is 6/255 approx 0.02352 (just outside 0.02 limit)

        thumb_A = bytes([100] * GRAY_BYTE_LENGTH)
        thumb_D = bytes([100+5] * GRAY_BYTE_LENGTH) # Diff 5/255 = 0.01960 <= 0.02 (match)
        thumb_E = bytes([100+6] * GRAY_BYTE_LENGTH) # Diff 6/255 = 0.02352 > 0.02 (no match)

        vid_A = self._create_mock_entry("vidA_sim.mp4", duration=100.0, gray_bytes={50.0: thumb_A})
        vid_D = self._create_mock_entry("vidD_sim.mp4", duration=100.0, gray_bytes={50.0: thumb_D})
        vid_E = self._create_mock_entry("vidE_sim.mp4", duration=100.0, gray_bytes={50.0: thumb_E})

        outcome_AD = check_if_entries_are_duplicate(vid_A, vid_D, settings)
        self.assertIsNotNone(outcome_AD, "AD similarity should be a match (diff 0.01960 vs limit 0.02)")
        if outcome_AD:
            self.assertAlmostEqual(outcome_AD[0], 1.0 - (5.0/255.0), places=5)

        outcome_AE = check_if_entries_are_duplicate(vid_A, vid_E, settings)
        self.assertIsNone(outcome_AE, "AE similarity should not be a match (diff 0.02352 vs limit 0.02)")


    def test_check_if_entries_are_duplicate_videos_missing_thumbnails(self):
        settings = CoreSettings(thumbnail_positions=[ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)])
        thumb_A = b'\xAA' * GRAY_BYTE_LENGTH

        vid1 = self._create_mock_entry("vid_ok.mp4", duration=100.0, gray_bytes={50.0: thumb_A})
        vid2_missing = self._create_mock_entry("vid_missing_thumb.mp4", duration=100.0, gray_bytes={}) # No key 50.0

        self.assertIsNone(check_if_entries_are_duplicate(vid1, vid2_missing, settings))
        self.assertIsNone(check_if_entries_are_duplicate(vid2_missing, vid1, settings))

        # Both missing (but comparison shouldn't even be called if entries are invalid for scan)
        vid3_also_missing = self._create_mock_entry("vid_also_missing.mp4", duration=100.0, gray_bytes={})
        self.assertIsNone(check_if_entries_are_duplicate(vid2_missing, vid3_also_missing, settings))

if __name__ == '__main__':
    unittest.main()

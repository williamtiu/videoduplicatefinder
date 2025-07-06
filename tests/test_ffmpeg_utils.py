import unittest
from unittest.mock import patch, MagicMock # For mocking ffmpeg calls
import os
import sys

# Ensure vdf_core is in path for testing
project_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root_dir)

from vdf_core.settings import CoreSettings, ThumbnailPositionSetting, ThumbnailPositionType, HardwareAcceleration
from vdf_core.file_entry import FileEntry, MediaInfo, EntryFlags
from vdf_core.ffmpeg_utils import (
    _calculate_thumbnail_timestamps_seconds,
    get_media_info,
    extract_gray_bytes_for_file_entry
)
from vdf_core.image_utils import GRAY_BYTE_LENGTH


# Mock __post_init__ for FileEntry to avoid file system access during these tests
# This is important as ffmpeg_utils might create/modify FileEntry instances
original_file_entry_post_init = FileEntry.__post_init__
def mock_file_entry_post_init_for_ffmpeg_tests(self_fe):
    if not hasattr(self_fe, 'path') or not self_fe.path: self_fe.path = "dummy/path.vid"
    self_fe.path = os.path.abspath(self_fe.path)
    if not hasattr(self_fe, 'flags'): self_fe.flags = EntryFlags.NONE
    if not hasattr(self_fe, 'gray_bytes_data'): self_fe.gray_bytes_data = {}
    # Set IS_IMAGE based on common extensions for tests that might need it
    if self_fe.path.lower().endswith((".jpg", ".png", ".jpeg")):
        self_fe.flags |= EntryFlags.IS_IMAGE

FileEntry.__post_init__ = mock_file_entry_post_init_for_ffmpeg_tests


class TestFfmpegUtilsCalculations(unittest.TestCase):
    def test_calculate_thumbnail_timestamps_seconds(self):
        positions = [
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 10.0),
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 90.0),
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, 5.0),
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_END, 15.0),
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0), # Duplicate for uniqueness test
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)
        ]
        duration = 100.0
        expected = sorted(list(set([5.0, 10.0, 50.0, 85.0, 90.0]))) # 100-15=85
        self.assertEqual(_calculate_thumbnail_timestamps_seconds(duration, positions), expected)

        # Test clamping
        positions_clamping = [
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, 120.0), # > duration
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_END, 110.0)    # results in < 0
        ]
        expected_clamping = sorted(list(set([0.0, 100.0]))) # 100-110 = -10 -> 0; 120 -> 100
        self.assertEqual(_calculate_thumbnail_timestamps_seconds(duration, positions_clamping), expected_clamping)

        self.assertEqual(_calculate_thumbnail_timestamps_seconds(0.0, positions), []) # Zero duration
        self.assertEqual(_calculate_thumbnail_timestamps_seconds(100.0, []), []) # No positions

        # Test with very short duration
        short_duration = 0.5
        positions_short = [
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0), # 0.25
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, 0.1), # 0.1
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_END, 0.1) # 0.4
        ]
        expected_short = sorted([0.1, 0.25, 0.4])
        self.assertEqual(_calculate_thumbnail_timestamps_seconds(short_duration, positions_short), expected_short)

        # Test with offsets that result in same timestamp due to clamping
        positions_same_clamp = [
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, 120.0), # -> 100
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 100.0)    # -> 100
        ]
        expected_same_clamp = [100.0] # Only one unique value after clamping
        self.assertEqual(_calculate_thumbnail_timestamps_seconds(100.0, positions_same_clamp), expected_same_clamp)


@patch('vdf_core.ffmpeg_utils.ffmpeg') # Mock the entire ffmpeg module used by ffmpeg_utils
class TestFfmpegUtilsApiCalls(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        # Restore original __post_init__ after all tests in this class are done
        FileEntry.__post_init__ = original_file_entry_post_init

    def test_get_media_info_success(self, mock_ffmpeg):
        dummy_path = os.path.abspath("dummy.mp4")
        settings = CoreSettings()

        mock_probe_data = {
            "format": {
                "format_name": "mov,mp4",
                "duration": "120.500000",
                "size": "1000000",
                "bit_rate": "66378"
            },
            "streams": [
                {
                    "index": 0, "codec_type": "video", "codec_name": "h264",
                    "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
                    "duration": "120.480000"
                },
                {
                    "index": 1, "codec_type": "audio", "codec_name": "aac",
                    "sample_rate": "48000", "channels": 2
                }
            ]
        }
        mock_ffmpeg.probe.return_value = mock_probe_data

        # Mock os.path.exists to always return True for this test
        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True):
            media_info = get_media_info(dummy_path, settings)

        self.assertIsNotNone(media_info)
        mock_ffmpeg.probe.assert_called_once_with(dummy_path, show_format=None, show_streams=None)

        self.assertEqual(media_info.format_name, "mov,mp4")
        self.assertAlmostEqual(media_info.duration_seconds, 120.5)
        self.assertEqual(media_info.size_bytes, 1000000)
        self.assertEqual(len(media_info.streams), 2)
        self.assertEqual(media_info.streams[0].codec_type, "video")
        self.assertEqual(media_info.streams[0].width, 1920)
        self.assertAlmostEqual(media_info.streams[0].frame_rate_avg, 30000/1001)
        self.assertEqual(media_info.streams[1].codec_type, "audio")
        self.assertEqual(media_info.streams[1].sample_rate, 48000)

    def test_get_media_info_minimal_data(self, mock_ffmpeg):
        dummy_path = os.path.abspath("minimal.mkv")
        settings = CoreSettings()
        mock_probe_data = { # format block is missing, duration only in stream
            "streams": [{
                "index": 0, "codec_type": "video", "width": 640, "height": 480,
                "duration": "10.0", # Duration from stream
                "avg_frame_rate": "0/0", # Test fallback
                "r_frame_rate": "25/1"
            }]
        }
        mock_ffmpeg.probe.return_value = mock_probe_data
        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True):
            media_info = get_media_info(dummy_path, settings)

        self.assertIsNotNone(media_info)
        self.assertAlmostEqual(media_info.duration_seconds, 10.0) # Should pick up from stream
        self.assertEqual(media_info.streams[0].width, 640)
        self.assertAlmostEqual(media_info.streams[0].frame_rate_avg, 25.0) # Fallback to r_frame_rate

    def test_get_media_info_malformed_numbers(self, mock_ffmpeg):
        dummy_path = os.path.abspath("malformed.mp4")
        settings = CoreSettings()
        mock_probe_data = {
            "format": {"duration": "not_a_number", "size": "100M", "bit_rate": "NA"},
            "streams": [{"index": 0, "codec_type": "video", "width": "720p"}] # width not int
        }
        mock_ffmpeg.probe.return_value = mock_probe_data
        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True):
            media_info = get_media_info(dummy_path, settings)

        self.assertIsNotNone(media_info) # Should still create object
        self.assertIsNone(media_info.duration_seconds) # Malformed numbers should result in None
        self.assertIsNone(media_info.size_bytes)
        self.assertIsNone(media_info.bit_rate_bps)
        self.assertEqual(len(media_info.streams), 1)
        self.assertIsNone(media_info.streams[0].width) # Malformed width

    def test_get_media_info_no_streams(self, mock_ffmpeg):
        dummy_path = os.path.abspath("no_streams.dat")
        settings = CoreSettings()
        mock_probe_data = {"format": {"duration": "5.0"}} # No streams array
        mock_ffmpeg.probe.return_value = mock_probe_data
        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True):
            media_info = get_media_info(dummy_path, settings)

        self.assertIsNotNone(media_info)
        self.assertAlmostEqual(media_info.duration_seconds, 5.0)
        self.assertEqual(len(media_info.streams), 0)


    def test_get_media_info_ffprobe_error(self, mock_ffmpeg):
        dummy_path = os.path.abspath("error.mp4")
        settings = CoreSettings(extended_ffmpeg_logging=True)

        # Simulate ffmpeg.Error from probe call
        mock_ffmpeg.Error = Exception # Base Exception for simplicity if specific type isn't needed
        mock_ffmpeg.probe.side_effect = mock_ffmpeg.Error("ffprobe error stderr data")

        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True):
            media_info = get_media_info(dummy_path, settings)

        self.assertIsNone(media_info)
        # (Could also check logger output if logger is injectable or mockable)

    def test_get_media_info_file_not_found(self, mock_ffmpeg):
        settings = CoreSettings()
        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=False):
            media_info = get_media_info("nonexistent.mp4", settings)
        self.assertIsNone(media_info)
        mock_ffmpeg.probe.assert_not_called()


    def test_extract_gray_bytes_image_success(self, mock_ffmpeg):
        dummy_image_path = os.path.abspath("test.jpg")
        entry = FileEntry(path=dummy_image_path) # is_image flag set by mock_post_init
        settings = CoreSettings()

        mock_ffmpeg_process = MagicMock()
        mock_ffmpeg_process.returncode = 0
        # Simulate ffmpeg outputting 256 bytes for a 16x16 grayscale image
        mock_ffmpeg_process.communicate.return_value = (b'\xAB' * GRAY_BYTE_LENGTH, b'')

        # Mock the chain: ffmpeg.input(...).output(...).global_args(...).run_async(...)
        mock_ffmpeg.input.return_value.output.return_value.global_args.return_value.run_async.return_value = mock_ffmpeg_process

        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True), \
             patch('vdf_core.image_utils.are_gray_bytes_too_dark', return_value=False) as mock_too_dark:
            success = extract_gray_bytes_for_file_entry(entry, settings)

        self.assertTrue(success)
        self.assertIn(0.0, entry.gray_bytes_data)
        self.assertEqual(entry.gray_bytes_data[0.0], b'\xAB' * GRAY_BYTE_LENGTH)
        self.assertFalse(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertFalse(entry.flags & EntryFlags.TOO_DARK)
        mock_too_dark.assert_called_once_with(b'\xAB' * GRAY_BYTE_LENGTH)

        # Check ffmpeg call structure
        mock_ffmpeg.input.assert_called_with(dummy_image_path)
        # Output args for image
        output_args = mock_ffmpeg.input.return_value.output.call_args[0]
        self.assertEqual(output_args[0], 'pipe:') # Output to pipe
        output_kwargs = mock_ffmpeg.input.return_value.output.call_args[1]
        self.assertEqual(output_kwargs['format'], 'rawvideo')
        self.assertEqual(output_kwargs['pix_fmt'], 'gray')
        self.assertEqual(output_kwargs['s'], '16x16')
        self.assertEqual(output_kwargs['vframes'], 1)


    def test_extract_gray_bytes_video_success_one_thumb(self, mock_ffmpeg):
        dummy_video_path = os.path.abspath("video.mp4")
        entry = FileEntry(path=dummy_video_path)
        entry.media_info = MediaInfo(duration_seconds=100.0) # Needs duration

        settings = CoreSettings(
            thumbnail_positions=[ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)],
            # No custom_ffmpeg_arguments or hwaccel for this basic test
        )
        # Expected timestamp for 50% of 100s is 50.0s
        expected_ts_key = 50.0

        mock_ffmpeg_process = MagicMock()
        mock_ffmpeg_process.returncode = 0
        mock_ffmpeg_process.communicate.return_value = (b'\xCD' * GRAY_BYTE_LENGTH, b'')
        mock_ffmpeg.input.return_value.output.return_value.global_args.return_value.run_async.return_value = mock_ffmpeg_process

        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True), \
             patch('vdf_core.image_utils.are_gray_bytes_too_dark', return_value=False) as mock_too_dark:
            success = extract_gray_bytes_for_file_entry(entry, settings)

        self.assertTrue(success)
        self.assertIn(expected_ts_key, entry.gray_bytes_data)
        self.assertEqual(entry.gray_bytes_data[expected_ts_key], b'\xCD' * GRAY_BYTE_LENGTH)
        self.assertFalse(entry.flags & EntryFlags.THUMBNAIL_ERROR)
        self.assertFalse(entry.flags & EntryFlags.TOO_DARK) # Since mock_too_dark returns False
        mock_too_dark.assert_called_once_with(b'\xCD' * GRAY_BYTE_LENGTH)

        # Check ffmpeg input call args for seeking ('ss')
        input_call_args = mock_ffmpeg.input.call_args[1]
        self.assertEqual(input_call_args['ss'], expected_ts_key)
        # Check output args (should be same as image for raw gray bytes)
        output_kwargs = mock_ffmpeg.input.return_value.output.call_args[1]
        self.assertEqual(output_kwargs['format'], 'rawvideo')
        self.assertEqual(output_kwargs['pix_fmt'], 'gray')


    def test_extract_gray_bytes_video_ffmpeg_fails_for_one_thumb(self, mock_ffmpeg):
        dummy_video_path = os.path.abspath("vid_fail.mp4")
        entry = FileEntry(path=dummy_video_path)
        entry.media_info = MediaInfo(duration_seconds=20.0)
        settings = CoreSettings(
            thumbnail_positions=[
                ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 10.0), # ts=2.0
                ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 90.0)  # ts=18.0
            ]
        )

        # Simulate ffmpeg succeeding for the first, failing for the second
        mock_process_success = MagicMock()
        mock_process_success.returncode = 0
        mock_process_success.communicate.return_value = (b'\xAA' * GRAY_BYTE_LENGTH, b'')

        mock_process_fail = MagicMock()
        mock_process_fail.returncode = 1 # Error
        mock_process_fail.communicate.return_value = (b'', b'ffmpeg error output')

        # Set up side_effect for run_async
        # This will be called twice, once for each thumbnail position
        mock_ffmpeg.input.return_value.output.return_value.global_args.return_value.run_async.side_effect = [
            mock_process_success,
            mock_process_fail
        ]

        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True), \
             patch('vdf_core.image_utils.are_gray_bytes_too_dark', return_value=False): # Assume not too dark
            success = extract_gray_bytes_for_file_entry(entry, settings)

        self.assertTrue(success) # Overall call considered success if at least one thumbnail is extracted
        self.assertTrue(entry.flags & EntryFlags.THUMBNAIL_ERROR) # But error flag should be set

        self.assertIn(2.0, entry.gray_bytes_data) # First one succeeded
        self.assertEqual(entry.gray_bytes_data[2.0], b'\xAA' * GRAY_BYTE_LENGTH)
        self.assertIn(18.0, entry.gray_bytes_data) # Second one failed
        self.assertIsNone(entry.gray_bytes_data[18.0])


    def test_extract_gray_bytes_video_all_thumbs_too_dark(self, mock_ffmpeg):
        dummy_video_path = os.path.abspath("dark_vid.mp4")
        entry = FileEntry(path=dummy_video_path)
        entry.media_info = MediaInfo(duration_seconds=10.0)
        settings = CoreSettings(thumbnail_positions=[
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)
        ])

        mock_ffmpeg_process = MagicMock()
        mock_ffmpeg_process.returncode = 0
        mock_ffmpeg_process.communicate.return_value = (b'\x01' * GRAY_BYTE_LENGTH, b'') # Valid but dark data
        mock_ffmpeg.input.return_value.output.return_value.global_args.return_value.run_async.return_value = mock_ffmpeg_process

        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True), \
             patch('vdf_core.image_utils.are_gray_bytes_too_dark', return_value=True) as mock_too_dark: # All thumbs are dark
            success = extract_gray_bytes_for_file_entry(entry, settings)

        self.assertTrue(success) # Extraction itself was successful
        self.assertTrue(entry.flags & EntryFlags.TOO_DARK)
        self.assertFalse(entry.flags & EntryFlags.THUMBNAIL_ERROR) # No ffmpeg error
        self.assertIn(5.0, entry.gray_bytes_data)
        mock_too_dark.assert_called_once()


    def test_extract_gray_bytes_uses_custom_ffmpeg_args_and_hwaccel(self, mock_ffmpeg):
        dummy_video_path = os.path.abspath("hw_test.mkv")
        entry = FileEntry(path=dummy_video_path)
        entry.media_info = MediaInfo(duration_seconds=10.0)
        settings = CoreSettings(
            thumbnail_positions=[ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)],
            custom_ffmpeg_arguments="-threads 2 -some_other_arg",
            hardware_acceleration_mode=HardwareAcceleration.CUDA
        )

        mock_ffmpeg_process = MagicMock()
        mock_ffmpeg_process.returncode = 0
        mock_ffmpeg_process.communicate.return_value = (b'\xBB' * GRAY_BYTE_LENGTH, b'')
        mock_ffmpeg.input.return_value.output.return_value.global_args.return_value.run_async.return_value = mock_ffmpeg_process

        with patch('vdf_core.ffmpeg_utils.os.path.exists', return_value=True), \
             patch('vdf_core.image_utils.are_gray_bytes_too_dark', return_value=False):
            extract_gray_bytes_for_file_entry(entry, settings)

        # Check input arguments for hwaccel
        input_call_args = mock_ffmpeg.input.call_args[1]
        self.assertEqual(input_call_args['hwaccel'], 'cuda')

        # Check global arguments for custom args
        global_args_call = mock_ffmpeg.input.return_value.output.return_value.global_args.call_args[0][0]
        self.assertIn('-threads', global_args_call)
        self.assertIn('2', global_args_call)
        self.assertIn('-some_other_arg', global_args_call)
        self.assertIn('-hide_banner', global_args_call) # Default global arg


if __name__ == '__main__':
    unittest.main()

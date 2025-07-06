import unittest
import os
import platform
from datetime import datetime, timezone
import base64

# Ensure vdf_core is in path for testing
import sys
project_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root_dir)

from vdf_core.file_entry import FileEntry, MediaInfo, StreamInfo, EntryFlags
from vdf_core.utils import get_image_extensions # For IS_IMAGE flag check

# Create a dummy file for stat testing
DUMMY_FILE_NAME = "dummy_test_file.txt"
DUMMY_IMAGE_FILE_NAME = "dummy_test_image.jpg"

def create_dummy_file(filename=DUMMY_FILE_NAME):
    with open(filename, "w") as f:
        f.write("test content")

def remove_dummy_file(filename=DUMMY_FILE_NAME):
    if os.path.exists(filename):
        os.remove(filename)

class TestFileEntry(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create_dummy_file(DUMMY_FILE_NAME)
        create_dummy_file(DUMMY_IMAGE_FILE_NAME) # Treat as image by extension

    @classmethod
    def tearDownClass(cls):
        remove_dummy_file(DUMMY_FILE_NAME)
        remove_dummy_file(DUMMY_IMAGE_FILE_NAME)

    def test_file_entry_creation_and_post_init(self):
        abs_path = os.path.abspath(DUMMY_FILE_NAME)
        entry = FileEntry(path=DUMMY_FILE_NAME)

        self.assertEqual(entry.path, abs_path)
        self.assertEqual(entry.folder, os.path.dirname(abs_path))
        self.assertEqual(entry.filename, DUMMY_FILE_NAME)

        self.assertIsNotNone(entry.date_created_utc)
        self.assertIsNotNone(entry.date_modified_utc)
        self.assertGreater(entry.file_size_bytes, 0)

        self.assertFalse(entry.flags & EntryFlags.IS_IMAGE)

        # Test with image extension
        img_entry = FileEntry(path=DUMMY_IMAGE_FILE_NAME)
        self.assertTrue(img_entry.flags & EntryFlags.IS_IMAGE)

        # Test non-existent file
        non_existent_entry = FileEntry(path="non_existent_file.mp4")
        self.assertTrue(non_existent_entry.flags & EntryFlags.METADATA_ERROR)
        self.assertTrue(non_existent_entry.is_invalid_for_scan)


    def test_file_entry_hashing_and_equality(self):
        entry1_path = os.path.abspath("test_video.mp4")
        entry2_path = os.path.abspath("test_video.MP4") # Different case
        entry3_path = os.path.abspath("another_video.mkv")

        # Create dummy FileEntry instances (don't need actual files for this test part)
        # We can override __post_init__ temporarily or pass dummy stat info
        # For simplicity, we'll rely on path only for hash/eq, assuming post_init runs
        # but actual file existence isn't critical for hash/eq test itself.

        # To avoid FileNotFoundError in __post_init__ for these non-existent paths:
        original_post_init = FileEntry.__post_init__
        FileEntry.__post_init__ = lambda self: None # Temporarily disable post_init

        entry1 = FileEntry(path=entry1_path)
        entry1.path = entry1_path # ensure path is set after disabling __post_init__

        entry1_dup = FileEntry(path=entry1_path)
        entry1_dup.path = entry1_path

        entry2 = FileEntry(path=entry2_path)
        entry2.path = entry2_path

        entry3 = FileEntry(path=entry3_path)
        entry3.path = entry3_path

        FileEntry.__post_init__ = original_post_init # Restore

        self.assertEqual(entry1, entry1_dup)
        self.assertEqual(hash(entry1), hash(entry1_dup))

        if platform.system() == "Windows":
            self.assertEqual(entry1, entry2, "Path comparison should be case-insensitive on Windows")
            self.assertEqual(hash(entry1), hash(entry2), "Path hashing should be case-insensitive on Windows")
        else:
            self.assertNotEqual(entry1, entry2, "Path comparison should be case-sensitive on non-Windows")
            # Hashes might collide, but typically different for different cased strings on sensitive systems
            # self.assertNotEqual(hash(entry1), hash(entry2)) # This is not guaranteed

        self.assertNotEqual(entry1, entry3)
        self.assertNotEqual(hash(entry1), hash(entry3)) # Less likely to collide for completely different paths


    def test_stream_info_serialization(self):
        si = StreamInfo(
            index=0, codec_type="video", codec_name="h264", width=1920, height=1080,
            pix_fmt="yuv420p", frame_rate_avg=29.97, bit_rate=5000000, duration=120.5
        )
        si_dict = si.to_dict()
        expected_dict = {
            'index': 0, 'codec_type': "video", 'codec_name': "h264",
            'width': 1920, 'height': 1080, 'pix_fmt': "yuv420p",
            'frame_rate_avg': 29.97, 'sample_rate': None, 'channels': None,
            'channel_layout': None, 'bit_rate': 5000000, 'duration': 120.5
        }
        self.assertEqual(si_dict, expected_dict)

        si_from_dict = StreamInfo.from_dict(expected_dict)
        self.assertIsNotNone(si_from_dict)
        self.assertEqual(si_from_dict.index, si.index)
        self.assertEqual(si_from_dict.width, si.width)
        self.assertEqual(si_from_dict.frame_rate_avg, si.frame_rate_avg)

        self.assertIsNone(StreamInfo.from_dict(None))


    def test_media_info_serialization(self):
        video_stream = StreamInfo(index=0, codec_type="video", codec_name="h264", width=1280, height=720)
        audio_stream = StreamInfo(index=1, codec_type="audio", codec_name="aac", sample_rate=48000)

        mi = MediaInfo(
            format_name="mp4", format_long_name="MP4 format", duration_seconds=180.25,
            size_bytes=50000000, bit_rate_bps=2219140,
            streams=[video_stream, audio_stream]
        )
        mi_dict = mi.to_dict()

        self.assertEqual(mi_dict['format_name'], "mp4")
        self.assertEqual(mi_dict['duration_seconds'], 180.25)
        self.assertEqual(len(mi_dict['streams']), 2)
        self.assertEqual(mi_dict['streams'][0]['codec_name'], "h264")
        self.assertEqual(mi_dict['streams'][1]['sample_rate'], 48000)

        mi_from_dict = MediaInfo.from_dict(mi_dict)
        self.assertIsNotNone(mi_from_dict)
        self.assertEqual(mi_from_dict.format_name, mi.format_name)
        self.assertEqual(mi_from_dict.duration_seconds, mi.duration_seconds)
        self.assertEqual(len(mi_from_dict.streams), len(mi.streams))
        self.assertEqual(mi_from_dict.streams[0].codec_name, mi.streams[0].codec_name)
        self.assertEqual(mi_from_dict.streams[0].width, mi.streams[0].width)
        self.assertEqual(mi_from_dict.streams[1].sample_rate, mi.streams[1].sample_rate)

        self.assertIsNone(MediaInfo.from_dict(None))


    def test_file_entry_serialization_minimal(self):
        # For FileEntry.from_dict to work without actual file for __post_init__
        original_post_init = FileEntry.__post_init__
        def mock_post_init(self_fe): # Minimal mock, path should be set before calling
            if not hasattr(self_fe, 'path') or not self_fe.path: self_fe.path = "dummy/path.vid"
            self_fe.path = os.path.abspath(self_fe.path) # Ensure path is absolute
            if get_image_extensions() and self_fe.path.endswith(tuple(get_image_extensions())):
                 self_fe.flags |= EntryFlags.IS_IMAGE
        FileEntry.__post_init__ = mock_post_init

        entry_path = os.path.abspath("test.mp4")
        entry = FileEntry(path=entry_path) # post_init is mocked
        entry.path = entry_path # ensure path is set correctly
        entry.flags = EntryFlags.IS_IMAGE | EntryFlags.TOO_DARK
        entry.file_size_bytes = 12345
        entry.thumb_config_signature = "test_sig_123"

        entry_dict = entry.to_dict()
        self.assertEqual(entry_dict['path'], entry_path)
        self.assertEqual(entry_dict['flags'], (EntryFlags.IS_IMAGE | EntryFlags.TOO_DARK).value)
        self.assertEqual(entry_dict['file_size_bytes'], 12345)
        self.assertEqual(entry_dict['thumb_config_signature'], "test_sig_123")

        entry_from_dict = FileEntry.from_dict(entry_dict)
        self.assertEqual(entry_from_dict.path, entry.path)
        self.assertEqual(entry_from_dict.flags, entry.flags)
        self.assertEqual(entry_from_dict.file_size_bytes, entry.file_size_bytes)
        self.assertEqual(entry_from_dict.thumb_config_signature, entry.thumb_config_signature)

        FileEntry.__post_init__ = original_post_init # Restore


    def test_file_entry_serialization_full(self):
        original_post_init = FileEntry.__post_init__
        def mock_post_init_full(self_fe): # Minimal mock for full test
            if not hasattr(self_fe, 'path') or not self_fe.path: self_fe.path = "dummy/path.vid"
            self_fe.path = os.path.abspath(self_fe.path)
            if get_image_extensions() and self_fe.path.endswith(tuple(get_image_extensions())):
                 self_fe.flags |= EntryFlags.IS_IMAGE

        FileEntry.__post_init__ = mock_post_init_full

        entry_path = os.path.abspath("video.mov")
        entry = FileEntry(path=entry_path)
        entry.path = entry_path

        entry.gray_bytes_data = {
            0.0: base64.b64decode("AQIDBA=="), # Example b64 for bytes [1,2,3,4]
            10.5: base64.b64decode("BQYHCA==")  # Example b64 for bytes [5,6,7,8]
        }
        entry.media_info = MediaInfo(
            format_name="mov", duration_seconds=60.0,
            streams=[StreamInfo(index=0, codec_type="video", width=640, height=480)]
        )
        entry.flags = EntryFlags.METADATA_ERROR
        entry.date_created_utc = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        entry.date_modified_utc = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        entry.file_size_bytes = 98765
        entry.thumb_config_signature = "sig_abc"

        entry_dict = entry.to_dict()
        # Check a few key serializations
        self.assertEqual(entry_dict['gray_bytes_data']['0.0'], "AQIDBA==")
        self.assertEqual(entry_dict['media_info']['format_name'], "mov")
        self.assertEqual(entry_dict['media_info']['streams'][0]['width'], 640)
        self.assertEqual(entry_dict['date_created_utc'], "2023-01-01T10:00:00+00:00")

        entry_from_dict = FileEntry.from_dict(entry_dict)
        self.assertEqual(entry_from_dict.path, entry.path)
        self.assertEqual(entry_from_dict.flags, entry.flags)
        self.assertEqual(entry_from_dict.file_size_bytes, entry.file_size_bytes)
        self.assertEqual(entry_from_dict.thumb_config_signature, entry.thumb_config_signature)
        self.assertEqual(entry_from_dict.date_created_utc, entry.date_created_utc)
        self.assertEqual(entry_from_dict.date_modified_utc, entry.date_modified_utc)

        self.assertIsNotNone(entry_from_dict.media_info)
        self.assertEqual(entry_from_dict.media_info.format_name, "mov")
        self.assertEqual(len(entry_from_dict.media_info.streams), 1)
        self.assertEqual(entry_from_dict.media_info.streams[0].width, 640)

        self.assertIn(0.0, entry_from_dict.gray_bytes_data)
        self.assertEqual(entry_from_dict.gray_bytes_data[0.0], base64.b64decode("AQIDBA=="))
        self.assertIn(10.5, entry_from_dict.gray_bytes_data)
        self.assertEqual(entry_from_dict.gray_bytes_data[10.5], base64.b64decode("BQYHCA=="))

        FileEntry.__post_init__ = original_post_init # Restore

if __name__ == '__main__':
    unittest.main()

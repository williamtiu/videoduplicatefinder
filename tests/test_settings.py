import unittest

from vdf_core.settings import ThumbnailPositionSetting, ThumbnailPositionType, get_thumbnail_config_signature

class TestSettings(unittest.TestCase):

    def test_get_thumbnail_config_signature_empty(self):
        self.assertEqual(get_thumbnail_config_signature([]), "no_thumbnails")

    def test_get_thumbnail_config_signature_single(self):
        positions = [ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)]
        self.assertEqual(get_thumbnail_config_signature(positions), "percentage:50.0")

    def test_get_thumbnail_config_signature_multiple_sorted(self):
        positions = [
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 10.0),
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, 5.0),
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 90.0)
        ]
        # Expected order after sorting by type name then value:
        # offset_from_start:5.0;percentage:10.0;percentage:90.0
        # Sorting is (type.value, value) -> ('offset_from_start', 5.0), ('percentage', 10.0), ('percentage', 90.0)
        expected = "offset_from_start:5.0;percentage:10.0;percentage:90.0"
        self.assertEqual(get_thumbnail_config_signature(positions), expected)

    def test_get_thumbnail_config_signature_multiple_unsorted(self):
        positions = [
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 90.0),
            ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, 5.0),
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 10.0)
        ]
        # Expected order after sorting:
        expected = "offset_from_start:5.0;percentage:10.0;percentage:90.0"
        self.assertEqual(get_thumbnail_config_signature(positions), expected)

    def test_get_thumbnail_config_signature_duplicate_types(self):
        positions = [
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0),
            ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 20.0)
        ]
        # Expected order:
        expected = "percentage:20.0;percentage:50.0"
        self.assertEqual(get_thumbnail_config_signature(positions), expected)

if __name__ == '__main__':
    unittest.main()

import unittest
from vdf_core.image_utils import (
    are_gray_bytes_too_dark,
    calculate_difference,
    flip_gray_bytes,
    GRAY_BYTE_LENGTH,
    DARK_PIXEL_MAX_VALUE,
    BRIGHT_PIXEL_MIN_VALUE,
    DARK_PIXEL_MAX_PERCENTAGE
)

class TestImageUtils(unittest.TestCase):

    def test_are_gray_bytes_too_dark(self):
        # Test None or invalid length input
        self.assertTrue(are_gray_bytes_too_dark(None))
        self.assertTrue(are_gray_bytes_too_dark(b'\x00' * (GRAY_BYTE_LENGTH - 1)))
        self.assertTrue(are_gray_bytes_too_dark(b'\x00' * (GRAY_BYTE_LENGTH + 1)))

        # Test "too dark" case: 97% of pixels are <= DARK_PIXEL_MAX_VALUE (10)
        dark_threshold_count = int(GRAY_BYTE_LENGTH * DARK_PIXEL_MAX_PERCENTAGE)

        # Exactly at threshold should be too dark
        dark_bytes_at_threshold = bytes([DARK_PIXEL_MAX_VALUE] * dark_threshold_count + \
                                        [DARK_PIXEL_MAX_VALUE + 1] * (GRAY_BYTE_LENGTH - dark_threshold_count))
        self.assertTrue(are_gray_bytes_too_dark(dark_bytes_at_threshold))

        # More than threshold should be too dark
        if dark_threshold_count < GRAY_BYTE_LENGTH : # Avoid if 100% is threshold
            dark_bytes_over_threshold = bytes([DARK_PIXEL_MAX_VALUE] * (dark_threshold_count + 1) + \
                                            [DARK_PIXEL_MAX_VALUE + 1] * (GRAY_BYTE_LENGTH - (dark_threshold_count + 1)))
            self.assertTrue(are_gray_bytes_too_dark(dark_bytes_over_threshold))

        # All pixels dark
        all_dark_bytes = bytes([0] * GRAY_BYTE_LENGTH)
        self.assertTrue(are_gray_bytes_too_dark(all_dark_bytes))

        # Test "not too dark" case: less than 97% dark pixels
        not_dark_count = dark_threshold_count - 1
        if not_dark_count >= 0:
            not_dark_bytes = bytes([DARK_PIXEL_MAX_VALUE] * not_dark_count + \
                                   [DARK_PIXEL_MAX_VALUE + 1] * (GRAY_BYTE_LENGTH - not_dark_count))
            self.assertFalse(are_gray_bytes_too_dark(not_dark_bytes))

        # All bright pixels (definitely not too dark)
        all_bright_bytes = bytes([255] * GRAY_BYTE_LENGTH)
        self.assertFalse(are_gray_bytes_too_dark(all_bright_bytes))

        # Mixed, but not enough dark pixels
        mixed_bytes = bytes([0, 5, 10] * 50 + [100, 150, 200] * (GRAY_BYTE_LENGTH // 3 - 50) + [255]*(GRAY_BYTE_LENGTH - (150 + (GRAY_BYTE_LENGTH // 3 - 50)*3)))
        mixed_bytes = mixed_bytes[:GRAY_BYTE_LENGTH] # ensure length
        if len(mixed_bytes) < GRAY_BYTE_LENGTH: mixed_bytes += b'\xff' * (GRAY_BYTE_LENGTH - len(mixed_bytes))

        dark_pixel_count_mixed = sum(1 for b in mixed_bytes if b <= DARK_PIXEL_MAX_VALUE)
        self.assertEqual(are_gray_bytes_too_dark(mixed_bytes), dark_pixel_count_mixed >= dark_threshold_count)


    def test_calculate_difference_invalid_input(self):
        valid_bytes = b'\x00' * GRAY_BYTE_LENGTH
        self.assertEqual(calculate_difference(None, valid_bytes, False, False), 1.0)
        self.assertEqual(calculate_difference(valid_bytes, None, False, False), 1.0)
        self.assertEqual(calculate_difference(b'\x01', valid_bytes, False, False), 1.0)
        self.assertEqual(calculate_difference(valid_bytes, b'\x01', False, False), 1.0)

    def test_calculate_difference_identical(self):
        bytes1 = bytes(range(GRAY_BYTE_LENGTH))
        self.assertEqual(calculate_difference(bytes1, bytes1, False, False), 0.0)

    def test_calculate_difference_completely_different(self):
        bytes1 = b'\x00' * GRAY_BYTE_LENGTH
        bytes2 = b'\xff' * GRAY_BYTE_LENGTH # Max value 255
        # Expected: (255 * 256) / 256 / 255 = 1.0
        self.assertEqual(calculate_difference(bytes1, bytes2, False, False), 1.0)

    def test_calculate_difference_half_different(self):
        # Create bytes1 as all 0s, bytes2 as half 0s and half 255s
        bytes1 = b'\x00' * GRAY_BYTE_LENGTH
        half_len = GRAY_BYTE_LENGTH // 2
        bytes2 = b'\x00' * half_len + bytes([255]) * (GRAY_BYTE_LENGTH - half_len)
        # Sum of diffs = (GRAY_BYTE_LENGTH - half_len) * 255
        # Comparable pixels = GRAY_BYTE_LENGTH
        # Expected diff = ((GRAY_BYTE_LENGTH - half_len) * 255) / GRAY_BYTE_LENGTH / 255
        # This simplifies to (GRAY_BYTE_LENGTH - half_len) / GRAY_BYTE_LENGTH
        expected = (GRAY_BYTE_LENGTH - half_len) / GRAY_BYTE_LENGTH
        self.assertAlmostEqual(calculate_difference(bytes1, bytes2, False, False), expected, places=5)

    def test_calculate_difference_ignore_black(self):
        # All black pixels, should result in 0.0 difference if ignored
        bytes_all_black1 = bytes([DARK_PIXEL_MAX_VALUE - 1] * GRAY_BYTE_LENGTH)
        bytes_all_black2 = bytes([DARK_PIXEL_MAX_VALUE - 2] * GRAY_BYTE_LENGTH)
        self.assertEqual(calculate_difference(bytes_all_black1, bytes_all_black2, True, False), 0.0)

        # Mixed: some black, some not. Black ones should be ignored.
        # P1: [0, 0, 100, 100], P2: [5, 5, 105, 105] (shortened for example)
        # If ignoring black, only (100,105) pairs are compared. Diff = (5+5)/2 / 255 = 5/255
        test_bytes1 = bytes([0,0,DARK_PIXEL_MAX_VALUE, DARK_PIXEL_MAX_VALUE] + [100]* (GRAY_BYTE_LENGTH - 4))
        test_bytes2 = bytes([5,5,DARK_PIXEL_MAX_VALUE-1, DARK_PIXEL_MAX_VALUE-2] + [105]* (GRAY_BYTE_LENGTH - 4))

        # Non-ignored pairs: (100, 105) repeated (GRAY_BYTE_LENGTH - 4) times
        # Sum of diffs = (GRAY_BYTE_LENGTH - 4) * 5
        # Comparable pixels = GRAY_BYTE_LENGTH - 4
        # Avg diff = 5. Percentage = 5 / 255.0
        if GRAY_BYTE_LENGTH - 4 > 0:
            expected_diff = 5.0 / 255.0
            self.assertAlmostEqual(calculate_difference(test_bytes1, test_bytes2, True, False), expected_diff, places=5)
        else: # if GRAY_BYTE_LENGTH is 4 or less, all pixels ignored
             self.assertEqual(calculate_difference(test_bytes1, test_bytes2, True, False), 0.0)


    def test_calculate_difference_ignore_white(self):
        # All white pixels
        bytes_all_white1 = bytes([BRIGHT_PIXEL_MIN_VALUE + 1] * GRAY_BYTE_LENGTH)
        bytes_all_white2 = bytes([BRIGHT_PIXEL_MIN_VALUE + 2] * GRAY_BYTE_LENGTH)
        self.assertEqual(calculate_difference(bytes_all_white1, bytes_all_white2, False, True), 0.0)

        # Mixed: some white, some not.
        test_bytes1 = bytes([255,255,BRIGHT_PIXEL_MIN_VALUE, BRIGHT_PIXEL_MIN_VALUE] + [100]* (GRAY_BYTE_LENGTH - 4))
        test_bytes2 = bytes([250,250,BRIGHT_PIXEL_MIN_VALUE+1, BRIGHT_PIXEL_MIN_VALUE+2] + [105]* (GRAY_BYTE_LENGTH - 4))
        if GRAY_BYTE_LENGTH - 4 > 0:
            expected_diff = 5.0 / 255.0
            self.assertAlmostEqual(calculate_difference(test_bytes1, test_bytes2, False, True), expected_diff, places=5)
        else:
            self.assertEqual(calculate_difference(test_bytes1, test_bytes2, False, True), 0.0)


    def test_calculate_difference_ignore_both(self):
        # Create an image that's black, white, and gray
        # P1: [0, 255, 128, 0], P2: [5, 250, 130, 5] (shortened example)
        # Ignored: (0,5), (255,250). Compared: (128,130). Diff = 2/1 / 255 = 2/255
        q = GRAY_BYTE_LENGTH // 3
        r = GRAY_BYTE_LENGTH % 3
        bytes1 = bytes([0]*q + [255]*q + [128]*(q+r))
        bytes2 = bytes([5]*q + [250]*q + [130]*(q+r))
        if q+r > 0 :
            expected_diff = 2.0 / 255.0
            self.assertAlmostEqual(calculate_difference(bytes1, bytes2, True, True), expected_diff, places=5)
        else: # All pixels were black or white
            self.assertEqual(calculate_difference(bytes1, bytes2, True, True), 0.0)

        # All pixels are either black or white
        bytes_bw1 = bytes([0, 255] * (GRAY_BYTE_LENGTH // 2) + ([0] if GRAY_BYTE_LENGTH % 2 else []))
        bytes_bw2 = bytes([5, 250] * (GRAY_BYTE_LENGTH // 2) + ([5] if GRAY_BYTE_LENGTH % 2 else []))
        self.assertEqual(calculate_difference(bytes_bw1, bytes_bw2, True, True), 0.0)


    def test_flip_gray_bytes_invalid(self):
        self.assertIsNone(flip_gray_bytes(None))
        self.assertIsNone(flip_gray_bytes(b"123"))

    def test_flip_gray_bytes_simple(self):
        # 16x16 grid, so 256 bytes.
        # Create a simple pattern: first row 0-15, second 16-31, etc.
        original = bytes(range(GRAY_BYTE_LENGTH))

        flipped = flip_gray_bytes(original)
        self.assertIsNotNone(flipped)
        self.assertEqual(len(flipped), GRAY_BYTE_LENGTH)

        # Check a few values from each row
        for row in range(16):
            # Original: row*16 + col
            # Flipped:  row*16 + (15-col)
            # So, flipped[row*16 + 0] should be original[row*16 + 15]
            # And  flipped[row*16 + 15] should be original[row*16 + 0]
            # And  flipped[row*16 + 7] should be original[row*16 + 8]
            self.assertEqual(flipped[row * 16 + 0], original[row * 16 + 15])
            self.assertEqual(flipped[row * 16 + 15], original[row * 16 + 0])
            self.assertEqual(flipped[row * 16 + 7], original[row * 16 + (15 - 7)]) # original[row*16 + 8]
            self.assertEqual(flipped[row * 16 + 8], original[row * 16 + (15 - 8)]) # original[row*16 + 7]

    def test_flip_gray_bytes_symmetric(self):
        # Create a horizontally symmetric pattern
        # e.g., each row is like [0,1,2,3,4,5,6,7,7,6,5,4,3,2,1,0]
        symmetric_row = bytes(list(range(8)) + list(range(7, -1, -1)))
        original_symmetric = b''.join([symmetric_row] * 16)

        flipped_symmetric = flip_gray_bytes(original_symmetric)
        self.assertEqual(original_symmetric, flipped_symmetric)

if __name__ == '__main__':
    unittest.main()

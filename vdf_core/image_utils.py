"""
Image utilities for pixel-level operations on grayscale data.
Based on VDF.Core/Utils/GrayBytesUtils.cs
"""
from typing import Optional

# Constants from GrayBytesUtils.cs
# private const int GrayByteLength = 256; // 16x16
# private const byte DarkPixelMaxValue = 10;
# private const byte BrightPixelMinValue = 245;
# private const double DarkPixelMaxPercent = 0.97d; // 97% of pixels can be dark

GRAY_BYTE_LENGTH = 256  # 16x16 pixels
DARK_PIXEL_MAX_VALUE = 10
BRIGHT_PIXEL_MIN_VALUE = 245 # Not used in the C# VerifyGrayScaleValues, but present in other methods
DARK_PIXEL_MAX_PERCENTAGE = 0.97 # 97% of pixels can be dark_pixels for the image to be "too dark"

def are_gray_bytes_too_dark(gray_bytes: Optional[bytes]) -> bool:
    """
    Checks if the provided 16x16 grayscale data is considered "too dark".
    Equivalent to GrayBytesUtils.VerifyGrayScaleValues() from C#.
    A thumbnail is "too dark" if more than DARK_PIXEL_MAX_PERCENTAGE (e.g., 97%)
    of its pixels have a value less than or equal to DARK_PIXEL_MAX_VALUE (e.g., 10).

    Args:
        gray_bytes: A byte string of 256 values (16x16 grayscale image).

    Returns:
        True if the image is considered too dark, False otherwise.
        Returns True if gray_bytes is None or not of the correct length,
        treating invalid input as "too dark" for safety.
    """
    if not gray_bytes or len(gray_bytes) != GRAY_BYTE_LENGTH:
        return True # Invalid input is considered too dark or problematic

    dark_pixel_count = 0
    for pixel_value in gray_bytes:
        if pixel_value <= DARK_PIXEL_MAX_VALUE:
            dark_pixel_count += 1

    if dark_pixel_count >= int(GRAY_BYTE_LENGTH * DARK_PIXEL_MAX_PERCENTAGE):
        return True # Too many dark pixels

    return False


def calculate_difference(
    gray_bytes1: Optional[bytes],
    gray_bytes2: Optional[bytes],
    ignore_black_pixels: bool,
    ignore_white_pixels: bool
) -> float:
    """
    Calculates the percentage difference between two 16x16 grayscale byte arrays.
    Equivalent to GrayBytesUtils.PercentageDifference and
    GrayBytesUtils.PercentageDifferenceWithoutSpecificPixels.

    Args:
        gray_bytes1: First grayscale data (256 bytes).
        gray_bytes2: Second grayscale data (256 bytes).
        ignore_black_pixels: If True, pixels <= DARK_PIXEL_MAX_VALUE are ignored in the diff calculation.
        ignore_white_pixels: If True, pixels >= BRIGHT_PIXEL_MIN_VALUE are ignored.

    Returns:
        A float between 0.0 (identical) and 1.0 (completely different).
        Returns 1.0 if inputs are invalid.
    """
    if not gray_bytes1 or len(gray_bytes1) != GRAY_BYTE_LENGTH or \
       not gray_bytes2 or len(gray_bytes2) != GRAY_BYTE_LENGTH:
        return 1.0  # Max difference if inputs are invalid

    diff_sum = 0
    comparable_pixels = 0

    for i in range(GRAY_BYTE_LENGTH):
        p1 = gray_bytes1[i]
        p2 = gray_bytes2[i]

        is_black_p1 = p1 <= DARK_PIXEL_MAX_VALUE
        is_white_p1 = p1 >= BRIGHT_PIXEL_MIN_VALUE

        # In C#, the logic for ignoring pixels was: if BOTH pixels are to be ignored, skip.
        # Or, more precisely, if (ignoreBlack && p1 is black && p2 is black) OR (ignoreWhite && p1 is white && p2 is white)
        # This seems to be what PercentageDifferenceWithoutSpecificPixels does by excluding from sum and count.
        # Let's refine this to match C# more closely.
        # The C# version skips a pixel for averaging if *either* pixel is an ignored color *and* that color is set to be ignored.
        # No, looking at GrayBytesUtils.PercentageDifferenceWithoutSpecificPixels:
        # it calculates sum of abs diffs, and a count.
        # It *skips* adding to sum and incrementing count if:
        #   (ignoreBlackPixels && (p1 <= 10 || p2 <= 10)) || (ignoreWhitePixels && (p1 >= 245 || p2 >= 245))
        # This means if we ignore black, and EITHER p1 or p2 is black, that pair is not counted.

        skip_pixel_pair = False
        if ignore_black_pixels and (is_black_p1 or p2 <= DARK_PIXEL_MAX_VALUE):
            skip_pixel_pair = True

        if not skip_pixel_pair and ignore_white_pixels and (is_white_p1 or p2 >= BRIGHT_PIXEL_MIN_VALUE):
            skip_pixel_pair = True

        if not skip_pixel_pair:
            diff_sum += abs(p1 - p2)
            comparable_pixels += 1

    if comparable_pixels == 0:
        # All pixels were ignored, or inputs were empty after filtering.
        # If original inputs were valid, this means they were e.g. all black and ignore_black was true.
        # In such a case, they are considered identical (0.0 difference).
        return 0.0

    # Max possible diff_sum is comparable_pixels * 255
    # Percentage difference is (actual_diff_sum / max_possible_diff_sum)
    return (diff_sum / comparable_pixels) / 255.0


def flip_gray_bytes(gray_bytes: Optional[bytes]) -> Optional[bytes]:
    """
    Horizontally flips a 16x16 grayscale byte array.
    Equivalent to GrayBytesUtils.FlipGrayScale.

    Args:
        gray_bytes: Grayscale data (256 bytes).

    Returns:
        Flipped grayscale data as a new bytes object, or None if input is invalid.
    """
    if not gray_bytes or len(gray_bytes) != GRAY_BYTE_LENGTH:
        return None

    flipped_data = bytearray(GRAY_BYTE_LENGTH)
    for row in range(16):
        for col in range(16):
            original_index = row * 16 + col
            flipped_index = row * 16 + (15 - col)
            flipped_data[flipped_index] = gray_bytes[original_index]

    return bytes(flipped_data)

# ```

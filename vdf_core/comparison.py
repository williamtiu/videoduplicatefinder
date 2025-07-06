"""
Core comparison logic for VideoDuplicateFinder Python.
Based on VDF.Core/ScanEngine.cs CheckIfDuplicate method and related logic.
"""
from typing import Tuple, Optional, Dict

from .file_entry import FileEntry, EntryFlags
from .settings import CoreSettings, ThumbnailPositionSetting, ThumbnailPositionType
from .image_utils import calculate_difference, flip_gray_bytes

# Represents the outcome of a comparison between two FileEntry objects
# (similarity_percent, was_flipped_better)
ComparisonOutcome = Tuple[float, bool]


def _calculate_expected_gray_bytes_key(
    pos_setting: ThumbnailPositionSetting,
    duration_seconds: float
) -> float:
    """
    Calculates the expected dictionary key (timestamp in seconds) for gray_bytes_data
    based on a ThumbnailPositionSetting and video duration.
    Mirrors logic from C# ScanEngine's CalculateExpectedGrayBytesKey.
    """
    if duration_seconds <= 0: # Should not happen if called with valid media_info
        return 0.0

    percentage_from_start = 0.0
    if pos_setting.type == ThumbnailPositionType.PERCENTAGE:
        percentage_from_start = pos_setting.value / 100.0
    elif pos_setting.type == ThumbnailPositionType.OFFSET_FROM_START:
        percentage_from_start = pos_setting.value / duration_seconds if duration_seconds > 0 else 0.0
    elif pos_setting.type == ThumbnailPositionType.OFFSET_FROM_END:
        time_from_start = duration_seconds - pos_setting.value
        percentage_from_start = time_from_start / duration_seconds if duration_seconds > 0 else 0.0

    # Clamp percentage to [0.0, 1.0]
    percentage_from_start = max(0.0, min(percentage_from_start, 1.0))
    return duration_seconds * percentage_from_start


def check_if_entries_are_duplicate(
    entry1: FileEntry,
    entry2: FileEntry,
    settings: CoreSettings
) -> Optional[ComparisonOutcome]:
    """
    Compares two FileEntry objects to determine if they are duplicates based on CoreSettings.

    Args:
        entry1: The first FileEntry.
        entry2: The second FileEntry.
        settings: The CoreSettings to use for comparison.

    Returns:
        A ComparisonOutcome (similarity_percent, was_flipped_match) if they are considered duplicates,
        otherwise None. Similarity is 0.0-1.0 (1.0 is identical).
    """

    # 1. Initial checks (type, duration for videos)
    if (entry1.flags & EntryFlags.IS_IMAGE) != (entry2.flags & EntryFlags.IS_IMAGE):
        return None # Cannot compare image with video

    if not (entry1.flags & EntryFlags.IS_IMAGE): # If they are videos
        if not entry1.media_info or not entry1.media_info.duration_seconds or \
           not entry2.media_info or not entry2.media_info.duration_seconds:
            return None # Missing duration info

        # Duration check
        dur1 = entry1.media_info.duration_seconds
        dur2 = entry2.media_info.duration_seconds
        if dur1 == 0 or dur2 == 0 : return None # Avoid division by zero if a duration is zero

        # Allow for a small tolerance for extremely short videos to prevent div by zero issues with diff percent
        # if dur1 < 0.1 or dur2 < 0.1:
        #     if abs(dur1 - dur2) > 0.1: # If very short, require almost exact duration
        #          return None

        # Percent duration difference check
        # (abs(dur1 - dur2) / max(dur1, dur2)) * 100 <= settings.duration_difference_percent
        # or equivalently:
        # min_dur / max_dur * 100 >= (100 - settings.duration_difference_percent)
        # (dur1 / dur2 * 100) should be within [100-P, 100+P]
        # This is what C# does: p = dur1 / dur2 * 100; if (p > maxP || p < minP) continue;

        # Ensure dur2 is not zero before division
        ratio = (dur1 / dur2) * 100.0
        min_allowed_ratio = 100.0 - settings.duration_difference_percent
        max_allowed_ratio = 100.0 + settings.duration_difference_percent

        if not (min_allowed_ratio <= ratio <= max_allowed_ratio):
            return None

    # 2. Thumbnail comparison
    difference_limit = settings.difference_limit # e.g. 0.04 for 96% similarity

    # Prepare entry1's thumbnails for comparison
    # For images, key is 0.0. For videos, keys are calculated.

    entry1_gray_bytes_to_compare: Dict[float, Optional[bytes]] = entry1.gray_bytes_data
    entry1_flipped_gray_bytes: Optional[Dict[float, Optional[bytes]]] = None

    if settings.compare_horizontally_flipped:
        entry1_flipped_gray_bytes = {}
        for key, gb_data in entry1.gray_bytes_data.items():
            if gb_data:
                entry1_flipped_gray_bytes[key] = flip_gray_bytes(gb_data)
            else:
                entry1_flipped_gray_bytes[key] = None


    def _compare_thumbnail_sets(
        thumb_set1: Dict[float, Optional[bytes]],
        thumb_set2: Dict[float, Optional[bytes]]
    ) -> Optional[float]: # Returns average difference, or None if comparison fails

        total_diff_sum = 0.0
        valid_comparisons = 0

        if entry1.flags & EntryFlags.IS_IMAGE: # Image comparison
            gb1 = thumb_set1.get(0.0)
            gb2 = thumb_set2.get(0.0)
            if gb1 and gb2:
                diff = calculate_difference(gb1, gb2,
                                            settings.ignore_black_pixels,
                                            settings.ignore_white_pixels)
                if diff > difference_limit:
                    return None # Single image thumbnail difference exceeds limit
                return diff # Return the single difference
            return None # Missing thumbnails for image comparison

        # Video comparison (multiple thumbnails)
        if not settings.thumbnail_positions: # No positions defined, cannot compare videos by thumbnail
            return None

        # Check if both entries have enough thumbnails corresponding to settings
        # This check should ideally be done before calling this function, by checking flags
        # For example, if entry.flags has THUMBNAIL_ERROR and not enough gray_bytes.

        for pos_setting in settings.thumbnail_positions:
            # This requires entry1's keys to align with pos_setting for this loop to make sense
            # The C# version calculates key for entry1 and entry2 based on *their respective durations*
            # and the *same pos_setting*.

            if not entry1.media_info or not entry1.media_info.duration_seconds or \
               not entry2.media_info or not entry2.media_info.duration_seconds:
                # This state should ideally be caught earlier
                return None

            key1 = _calculate_expected_gray_bytes_key(pos_setting, entry1.media_info.duration_seconds)
            key2 = _calculate_expected_gray_bytes_key(pos_setting, entry2.media_info.duration_seconds)

            gb1 = thumb_set1.get(key1)
            gb2 = thumb_set2.get(key2) # thumb_set2 is always entry2.gray_bytes_data

            if gb1 and gb2:
                single_diff = calculate_difference(gb1, gb2,
                                                 settings.ignore_black_pixels,
                                                 settings.ignore_white_pixels)

                if single_diff > difference_limit:
                    # For videos, if any single corresponding thumbnail pair is too different,
                    # the entire set is not a match. (This matches C# ScanEngine.CheckIfDuplicate)
                    return None

                total_diff_sum += single_diff
                valid_comparisons += 1
            else:
                # If a corresponding thumbnail is missing for any position, comparison fails for videos
                return None

        if not settings.thumbnail_positions: # No thumbnails were supposed to be compared
             if entry1.flags & EntryFlags.IS_IMAGE: # Should have been caught above
                  return None
             # For videos, if no thumbnail positions, what does it mean?
             # C# returns false if ThumbnailPositions.Count == 0 for videos.
             # So, if we reach here and it's a video with no positions, it's not a duplicate by this logic.
             return None


        if valid_comparisons == 0 and settings.thumbnail_positions:
            # This means thumbnails were expected, but none could be compared (e.g., all were None)
            return None

        if valid_comparisons > 0:
            return total_diff_sum / valid_comparisons # Average difference

        # If valid_comparisons is 0 and no thumbnail_positions (e.g. for images if logic was different)
        # or if somehow we get here for videos with 0 valid_comparisons but positions were defined.
        return None


    # Perform comparison with original entry1 thumbnails
    avg_diff_original: Optional[float] = _compare_thumbnail_sets(entry1_gray_bytes_to_compare, entry2.gray_bytes_data)
    is_duplicate_original = False
    if avg_diff_original is not None and avg_diff_original <= difference_limit:
        is_duplicate_original = True

    final_similarity_percent = 0.0
    was_flipped_match_better = False

    if is_duplicate_original:
        final_similarity_percent = (1.0 - avg_diff_original) # Convert average difference to similarity

    # Perform comparison with flipped entry1 thumbnails if applicable
    if settings.compare_horizontally_flipped and entry1_flipped_gray_bytes:
        avg_diff_flipped: Optional[float] = _compare_thumbnail_sets(entry1_flipped_gray_bytes, entry2.gray_bytes_data)
        is_duplicate_flipped = False
        if avg_diff_flipped is not None and avg_diff_flipped <= difference_limit:
            is_duplicate_flipped = True

        if is_duplicate_flipped:
            current_flipped_similarity = (1.0 - avg_diff_flipped)
            if not is_duplicate_original or current_flipped_similarity > final_similarity_percent:
                # Flipped is a match and either original was not, or flipped is better
                final_similarity_percent = current_flipped_similarity
                was_flipped_match_better = True
                # The primary "is_duplicate" status is now true if it wasn't already
                is_duplicate_original = True
            # If original was already a better or equal match, was_flipped_match_better remains false

    if is_duplicate_original : # This flag is true if either original or flipped was a match
        # Hard link check (conceptual, platform-dependent)
        if settings.exclude_hard_links and not (entry1.flags & EntryFlags.IS_IMAGE):
            # This check is complex: requires os.path.samefile(entry1.path, entry2.path) on POSIX
            # or checking file indices. For now, we'll assume this check would be done
            # in the ScanEngine if two files are found to be duplicates by this function.
            # C# version does: if (isDuplicate && entry.FileSize == compItem.FileSize && ...)
            # This means this function should first determine if it's a visual duplicate,
            # then the caller (ScanEngine) might apply the hardlink exclusion.
            # So, we don't filter out based on hardlinks here.
            pass

        return (final_similarity_percent, was_flipped_match_better)

    return None # Not a duplicate

# ```

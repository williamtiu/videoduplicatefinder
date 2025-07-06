"""
Basic Command-Line Interface for VideoDuplicateFinder (Python version)
- For testing the core logic.
"""
import argparse
import os
import sys
import logging # Import logging
from typing import List, Dict, Set

# Adjust path to import from vdf_core, assuming main_cli.py is in the project root
# and vdf_core is a subdirectory.
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from vdf_core.settings import CoreSettings, ThumbnailPositionSetting, ThumbnailPositionType
from vdf_core.file_entry import FileEntry, EntryFlags
from vdf_core.ffmpeg_utils import get_media_info, extract_gray_bytes_for_file_entry
from vdf_core.comparison import check_if_entries_are_duplicate, ComparisonOutcome
from vdf_core.utils import get_media_extensions, is_image_file, is_video_file
from vdf_core.database import DatabaseManager
from vdf_core.scan_engine import ScanEngine, ScanProgressEventArgs # For progress type hint

# Setup logger
logger = logging.getLogger("vdf_cli")
# Logger will be configured in main()


# No longer using global_file_database or simplified CLI functions.
# ScanEngine and DatabaseManager will be used directly.

# CLI-specific progress handler
def cli_progress_handler(args: ScanProgressEventArgs):
    # Simple progress display for CLI
    if args.max_value > 0:
        percent = (args.current_value / args.max_value) * 100
        progress_bar = f"[{'#' * int(percent // 10)}{'.' * (10 - int(percent // 10))}] {percent:.1f}%"
    else:
        progress_bar = "[No items to process or count unknown]"

    if args.current_file:
        logger.info(f"{args.phase}: {progress_bar} ({args.current_value}/{args.max_value}) - {os.path.basename(args.current_file)}")
    else:
        logger.info(f"{args.phase}: {progress_bar} ({args.current_value}/{args.max_value})")

    if args.estimated_remaining is not None and args.elapsed_time is not None:
        def format_cli_time(seconds):
            s = int(seconds)
            h, s = divmod(s, 3600)
            m, s = divmod(s, 60)
            return f"{h:02d}:{m:02d}:{s:02d}"
        logger.debug(f"Elapsed: {format_cli_time(args.elapsed_time)}, Remaining: ~{format_cli_time(args.estimated_remaining)}")


def old_build_file_list_for_cli(directories: List[str], settings: CoreSettings) -> List[FileEntry]:
    """
    Simplified file list builder for CLI testing.
    Scans directories, creates FileEntry objects, and populates basic info.
    Does not yet handle persistence or complex database interactions.
    """
    discovered_files: List[FileEntry] = []
    media_extensions = get_media_extensions()

    for scan_dir in directories:
        if not os.path.isdir(scan_dir):
            logger.warning(f"Directory not found: {scan_dir}")
            continue

        logger.info(f"Scanning directory: {scan_dir}")
        for root, _, files in os.walk(scan_dir):
            if not settings.include_subdirectories and root != scan_dir:
                logger.debug(f"Skipping subdirectory (not included): {root}")
                continue

            for filename in files:
                file_path = os.path.join(root, filename)

                if any(file_path.startswith(blacklisted_path) for blacklisted_path in settings.blacklist):
                    logger.debug(f"Skipping blacklisted file: {file_path}")
                    continue

                ext = os.path.splitext(filename)[1].lower()
                if ext not in media_extensions:
                    logger.debug(f"Skipping non-media file by extension: {file_path}")
                    continue

                if not settings.include_images and is_image_file(file_path):
                    logger.debug(f"Skipping image file (not included): {file_path}")
                    continue

                try:
                    entry = FileEntry(path=file_path)
                    if entry.file_size_bytes == 0 and not (entry.flags & EntryFlags.IS_IMAGE) :
                        logger.debug(f"Skipping zero-byte file: {file_path}")
                        continue

                    if settings.filter_by_file_size:
                        size_mb = entry.file_size_bytes / (1024 * 1024)
                        if size_mb < settings.minimum_file_size_mb or size_mb > settings.maximum_file_size_mb:
                            logger.debug(f"Skipping due to size filter: {file_path} ({size_mb:.2f}MB)")
                            continue

                    discovered_files.append(entry)
                except Exception as e:
                    logger.error(f"Error creating FileEntry for {file_path}: {e}", exc_info=settings.extended_ffmpeg_logging)

    return discovered_files

def gather_infos_for_cli(entries: List[FileEntry], settings: CoreSettings):
    """
    Simplified info gatherer for CLI. Populates media_info and gray_bytes.
    """
    total = len(entries)
    for i, entry in enumerate(entries):
        logger.info(f"Processing [{i+1}/{total}]: {entry.filename}")

        if not entry.media_info:
            logger.debug(f"  Fetching metadata for: {entry.filename}")
            entry.media_info = get_media_info(entry.path, settings)
            if not entry.media_info and not (entry.flags & EntryFlags.IS_IMAGE):
                entry.flags |= EntryFlags.METADATA_ERROR
                entry.is_invalid_for_scan = True
                logger.warning(f"  Failed to get metadata for: {entry.filename}")
                continue
            elif entry.media_info and (entry.media_info.duration_seconds is None or entry.media_info.duration_seconds <=0) and not (entry.flags & EntryFlags.IS_IMAGE):
                entry.flags |= EntryFlags.METADATA_ERROR
                entry.is_invalid_for_scan = True
                logger.warning(f"  No valid duration for: {entry.filename}")
                continue

        needs_thumbnails = True
        if not (entry.flags & EntryFlags.IS_IMAGE) and not settings.thumbnail_positions:
            logger.debug(f"  No thumbnail positions defined for video: {entry.filename}, skipping thumbnail extraction.")
            needs_thumbnails = False

        if needs_thumbnails and not entry.gray_bytes_data:
            logger.debug(f"  Extracting thumbnails for: {entry.filename}")
            success = extract_gray_bytes_for_file_entry(entry, settings)
            if not success:
                logger.warning(f"  Failed initial attempt to extract thumbnails for: {entry.filename}")
                entry.is_invalid_for_scan = True
                continue
            if not entry.gray_bytes_data and not (entry.flags & EntryFlags.TOO_DARK):
                entry.flags |= EntryFlags.THUMBNAIL_ERROR
                entry.is_invalid_for_scan = True
                logger.warning(f"  No thumbnails generated (and not too_dark) for: {entry.filename}")
                continue

        if entry.flags & EntryFlags.TOO_DARK:
            logger.info(f"  Thumbnails are too dark for: {entry.filename}")
            # entry.is_invalid_for_scan = True # Optionally skip if too dark based on a setting

        global_file_database[entry.path] = entry


def scan_for_duplicates_for_cli(
    entries: List[FileEntry],
    settings: CoreSettings
) -> Dict[str, List[FileEntry]]:
    """
    Simplified duplicate scanner for CLI.
    Returns a dictionary mapping a group_id to a list of duplicate FileEntry objects.
    """
    valid_entries_for_scan: List[FileEntry] = []
    for entry in entries:
        if entry.is_invalid_for_scan:
            logger.debug(f"Skipping invalid for scan: {entry.filename}")
            continue
        if entry.flags & EntryFlags.METADATA_ERROR:
            logger.debug(f"Skipping due to metadata error: {entry.filename}")
            continue
        # Allow THUMBNAIL_ERROR if some thumbnails exist, comparison logic will handle missing ones.
        # But if THUMBNAIL_ERROR is set AND no thumbnails, then skip.
        if entry.flags & EntryFlags.THUMBNAIL_ERROR and not entry.gray_bytes_data:
             logger.debug(f"Skipping due to thumbnail error and no thumbnails: {entry.filename}")
             continue

        if not (entry.flags & EntryFlags.IS_IMAGE) and not entry.gray_bytes_data and settings.thumbnail_positions:
            logger.debug(f"Skipping video with no thumbnails where expected: {entry.filename}")
            continue
        if (entry.flags & EntryFlags.IS_IMAGE) and not entry.gray_bytes_data.get(0.0):
            logger.debug(f"Skipping image with no thumbnail data: {entry.filename}")
            continue

        valid_entries_for_scan.append(entry)

    logger.info(f"\nStarting comparison for {len(valid_entries_for_scan)} valid files...")

    duplicates_found: Dict[str, List[FileEntry]] = {}
    assigned_to_group: Set[str] = set()

    total_comparisons = len(valid_entries_for_scan)
    for i in range(total_comparisons):
        entry1 = valid_entries_for_scan[i]
        logger.info(f"Comparing file {i+1}/{total_comparisons}: {entry1.filename}")

        for j in range(i + 1, total_comparisons):
            entry2 = valid_entries_for_scan[j]

            outcome: Optional[ComparisonOutcome] = check_if_entries_are_duplicate(entry1, entry2, settings)

            if outcome:
                similarity_percent, was_flipped = outcome
                logger.info(
                    f"  DUPLICATE: '{entry1.filename}' and '{entry2.filename}' "
                    f"(Similarity: {similarity_percent*100:.2f}%, Flipped: {was_flipped})"
                )

                group_id_e1 = None
                group_id_e2 = None
                for gid, items in duplicates_found.items():
                    if entry1.path in [item.path for item in items]: group_id_e1 = gid
                    if entry2.path in [item.path for item in items]: group_id_e2 = gid

                if group_id_e1 and group_id_e2 and group_id_e1 != group_id_e2:
                    logger.debug(f"Merging duplicate groups {group_id_e1} and {group_id_e2}")
                    duplicates_found[group_id_e1].extend(duplicates_found[group_id_e2])
                    # Update paths in assigned_to_group if necessary, though simple dict lookup above is by object
                    for item_in_g2 in duplicates_found[group_id_e2]:
                        assigned_to_group.discard(item_in_g2.path) # remove from old group tracking if any
                        assigned_to_group.add(item_in_g2.path) # ensure it's tracked with new group
                    del duplicates_found[group_id_e2]
                elif group_id_e1:
                    if entry2.path not in [item.path for item in duplicates_found[group_id_e1]]:
                        duplicates_found[group_id_e1].append(entry2)
                        assigned_to_group.add(entry2.path)
                elif group_id_e2:
                    if entry1.path not in [item.path for item in duplicates_found[group_id_e2]]:
                         duplicates_found[group_id_e2].append(entry1)
                         assigned_to_group.add(entry1.path)
                else:
                    new_group_id = f"group_{len(duplicates_found) + 1}"
                    logger.debug(f"Creating new duplicate group {new_group_id} for {entry1.filename} and {entry2.filename}")
                    duplicates_found[new_group_id] = [entry1, entry2]
                    assigned_to_group.add(entry1.path)
                    assigned_to_group.add(entry2.path)

    return duplicates_found


def main():
    parser = argparse.ArgumentParser(description="Video Duplicate Finder (CLI Test Version)")
    parser.add_argument("scan_paths", nargs='+', help="One or more directories to scan.")
    parser.add_argument(
        "--similarity", type=float, default=96.0, help="Similarity threshold (0-100, default: 96.0)"
    )
    parser.add_argument(
        "--duration-diff", type=float, default=20.0, help="Max duration difference %% (0-100, default: 20.0)"
    )
    parser.add_argument(
        "--include-images", action="store_true", help="Include images in the scan."
    )
    parser.add_argument(
        "--no-subdirectories", action="store_false", dest="include_subdirectories",
        help="Don't scan subdirectories."
    )
    parser.add_argument(
        "--compare-flipped", action="store_true", help="Compare horizontally flipped thumbnails."
    )
    parser.add_argument(
        "--thumb-positions", type=str, default="perc:50",
        help="Thumbnail positions, comma-separated. Format: type:value (e.g., perc:50,start:5,end:10)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose debug logging."
    )
    parser.add_argument(
        "--extended-ffmpeg-log", action="store_true", help="Enable extended FFmpeg/FFprobe error logging."
    )


    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        stream=sys.stderr) # Log to stderr like print_progress did

    settings = CoreSettings()
    settings.include_list = set(args.scan_paths)
    settings.similarity_threshold_percent = args.similarity
    settings.duration_difference_percent = args.duration_diff
    settings.include_images = args.include_images
    settings.include_subdirectories = args.include_subdirectories
    settings.compare_horizontally_flipped = args.compare_flipped
    settings.extended_ffmpeg_logging = args.extended_ffmpeg_log # Pass this to settings

    parsed_thumb_positions: List[ThumbnailPositionSetting] = []
    if args.thumb_positions:
        positions_str = args.thumb_positions.split(',')
        for pos_str in positions_str:
            try:
                type_str, val_str = pos_str.split(':')
                val = float(val_str)
                if type_str.lower() == "perc":
                    parsed_thumb_positions.append(ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, val))
                elif type_str.lower() == "start":
                    parsed_thumb_positions.append(ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_START, val))
                elif type_str.lower() == "end":
                    parsed_thumb_positions.append(ThumbnailPositionSetting(ThumbnailPositionType.OFFSET_FROM_END, val))
                else:
                    logger.warning(f"Unknown thumbnail position type '{type_str}' in '{pos_str}'")
            except ValueError:
                logger.warning(f"Invalid thumbnail position format '{pos_str}'")
        if parsed_thumb_positions:
             settings.thumbnail_positions = parsed_thumb_positions

    logger.info("Starting scan...")
    logger.info(f"Settings: Similarity: {settings.similarity_threshold_percent}%, Duration Diff: {settings.duration_difference_percent}%")
    logger.info(f"Thumbnail Positions: {[str(p) for p in settings.thumbnail_positions]}")
    logger.debug(f"Verbose logging enabled. FFmpeg extended logging: {settings.extended_ffmpeg_logging}")

    # Initialize DatabaseManager and ScanEngine
    db_manager = DatabaseManager(settings)
    scan_engine = ScanEngine(settings, db_manager)

    # Connect progress handler
    scan_engine.on_progress = cli_progress_handler

    # Define simple handlers for other events (can be expanded)
    scan_engine.on_scan_phase_changed = lambda phase: logger.info(f"--- Entering Phase: {phase} ---")
    scan_engine.on_files_enumerated = lambda count: logger.info(f"--- Files Enumerated: {count} ---")
    scan_engine.on_info_gathering_complete = lambda count: logger.info(f"--- Info Gathering Complete for: {count} files ---")

    scan_engine.on_scan_aborted = lambda: logger.error("--- SCAN ABORTED ---")

    final_duplicate_groups: Dict[str, Any] = {} # To store results from on_scan_complete

    def on_scan_complete_handler(dups_result: Dict[str, Any]):
        nonlocal final_duplicate_groups
        final_duplicate_groups = dups_result
        logger.info("--- SCAN COMPLETE (from handler) ---")

    scan_engine.on_scan_complete = on_scan_complete_handler

    # Start the scan
    # TODO: Add CLI arguments for quick_scan_if_possible, cleanup_db_only, etc.
    # For now, always full scan.
    try:
        scan_engine.start_scan(quick_scan_if_possible=False) # Set to False for full build/gather
    except Exception as e:
        logger.critical(f"Critical error during scan execution: {e}", exc_info=True)


    # Print results to stdout (final output)
    if not final_duplicate_groups:
        print("\nNo duplicates found.")
    else:
        print(f"\nFound {len(final_duplicate_groups)} duplicate group(s):")
        group_num = 1
        for group_id, group_obj in final_duplicate_groups.items():
            print(f"\nGroup {group_num} (ID: {group_id}):")
            # group_obj is DuplicateItemGroup, items are in group_obj.items_with_similarity or group_obj.items
            for item_entry, similarity in group_obj.items_with_similarity: # Assuming this structure
                # Best match flags can be accessed via group_obj.best_match_flags.get(item_entry.path, set())
                best_flags_str = ", ".join(sorted(list(group_obj.best_match_flags.get(item_entry.path, set()))))
                sim_str = f"{similarity*100:.2f}%" if similarity != 1.0 or len(group_obj.items_with_similarity) > 1 else "Reference"
                print(f"  - {item_entry.path} (Sim: {sim_str}, Size: {item_entry.file_size_bytes / (1024*1024):.2f}MB, Flags: {str(item_entry.flags)}, Best: [{best_flags_str}])")
            group_num += 1

    logger.info("\nCLI Scan Process Finished.")

if __name__ == "__main__":
    # Example usage:
    # python main_cli.py /path/to/videos --similarity 95 --include-images
    # python main_cli.py ./my_test_folder --thumb-positions "perc:10,perc:50,perc:90"
    main()
# ``` # This was the offending line

"""
ScanEngine for VideoDuplicateFinder (Python version).
Orchestrates the process of finding duplicate video/image files.
"""
import os
import time
import logging
import threading
from typing import List, Set, Dict, Optional, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from .settings import CoreSettings, get_thumbnail_config_signature
from .database import DatabaseManager
from .file_entry import FileEntry, EntryFlags # Assuming file_entry.py is in the same package
from .ffmpeg_utils import get_media_info, extract_gray_bytes_for_file_entry
from .comparison import check_if_entries_are_duplicate, ComparisonOutcome
from .utils import get_media_extensions, is_image_file # For file filtering

logger = logging.getLogger(__name__)

# Define placeholder for DuplicateItem and ScanProgressChangedEventArgs equivalent
# These would typically be dataclasses or simple classes
class DuplicateItemGroup:
    def __init__(self, group_id: str):
        self.group_id = group_id
        # Store tuples of (FileEntry, similarity_score_within_group)
        # Similarity score here is conceptual, needs to be defined how it's calculated by ScanEngine
        # For now, let's assume it's a float representing similarity to a reference/average.
        self.items_with_similarity: List[Tuple[FileEntry, float]] = []
        self.best_match_flags: Dict[str, Set[str]] = {} # path -> set of "IsBest..." flags

    @property
    def items(self) -> List[FileEntry]: # Keep original .items property for compatibility if needed elsewhere
        return [entry for entry, sim in self.items_with_similarity]

    def add_item(self, entry: FileEntry, similarity: float, is_flipped: bool = False): # Added similarity
        # is_flipped might be stored if relevant for display, or on a wrapper object
        if not any(e.path == entry.path for e, s in self.items_with_similarity):
            self.items_with_similarity.append((entry, similarity))

class ScanProgressEventArgs:
    def __init__(self, current_value: int, max_value: int, current_file: str = "", message: str = "",
                 phase: str = "", estimated_remaining: Optional[float] = None,
                 elapsed_time: Optional[float] = None):
        self.current_value = current_value
        self.max_value = max_value
        self.current_file = current_file
        self.message = message
        self.phase = phase # e.g., "Building File List", "Gathering Info", "Comparing"
        self.estimated_remaining = estimated_remaining # in seconds
        self.elapsed_time = elapsed_time # in seconds

ProgressCallback = Callable[[ScanProgressEventArgs], None]

class ScanEngine:
    def __init__(self, settings: CoreSettings, db_manager: DatabaseManager):
        self.settings = settings
        self.db_manager = db_manager

        self.duplicates: Dict[str, DuplicateItemGroup] = {} # group_id -> DuplicateItemGroup

        self._is_scanning: bool = False
        self._is_paused: threading.Event = threading.Event()
        self._cancel_requested: threading.Event = threading.Event()

        self._current_phase_progress: int = 0
        self._current_phase_max_value: int = 0
        self._phase_start_time: float = 0.0
        self._total_scan_start_time: float = 0.0

        # Callbacks for progress and events
        self.on_progress: Optional[ProgressCallback] = None
        self.on_scan_phase_changed: Optional[Callable[[str], None]] = None # Phase name
        self.on_scan_complete: Optional[Callable[[Dict[str, DuplicateItemGroup]], None]] = None
        self.on_scan_aborted: Optional[Callable[[], None]] = None
        self.on_files_enumerated: Optional[Callable[[int], None]] = None # count
        self.on_info_gathering_complete: Optional[Callable[[int], None]] = None # count

    def _check_pause_cancel(self):
        """Helper to check for pause or cancellation requests."""
        if self._cancel_requested.is_set():
            raise OperationCanceledError("Scan was cancelled by user.")
        while self._is_paused.is_set():
            if self._cancel_requested.is_set(): # Check again if cancelled while paused
                raise OperationCanceledError("Scan was cancelled by user while paused.")
            time.sleep(0.1) # Sleep briefly while paused

    def _report_progress(self, current_file: str = "", message: str = "", increment: bool = True, phase_override: Optional[str]=None):
        if increment:
            self._current_phase_progress += 1

        if self.on_progress:
            now = time.monotonic()
            elapsed_phase = now - self._phase_start_time
            estimated_remaining = None
            if self._current_phase_progress > 0 and self._current_phase_max_value > 0:
                time_per_item = elapsed_phase / self._current_phase_progress
                remaining_items = self._current_phase_max_value - self._current_phase_progress
                estimated_remaining = time_per_item * remaining_items

            phase = phase_override if phase_override else self._get_current_phase_name()

            args = ScanProgressEventArgs(
                current_value=self._current_phase_progress,
                max_value=self._current_phase_max_value,
                current_file=current_file,
                message=message,
                phase=phase,
                estimated_remaining=estimated_remaining,
                elapsed_time=now - self._total_scan_start_time
            )
            self.on_progress(args)

    def _reset_phase_progress(self, max_value: int, phase_name: str):
        self._current_phase_progress = 0
        self._current_phase_max_value = max_value
        self._phase_start_time = time.monotonic()
        if self.on_scan_phase_changed:
            self.on_scan_phase_changed(phase_name)
        logger.info(f"Starting phase: {phase_name} (Total items: {max_value})")

    def _get_current_phase_name(self) -> str:
        # This would be dynamically set based on current operation
        # For now, a placeholder. Will be set in start_scan() etc.
        return "Processing"


    def start_scan(self, quick_scan_if_possible: bool = False):
        if self._is_scanning:
            logger.warning("Scan is already in progress.")
            return

        self._is_scanning = True
        self._is_paused.clear()
        self._cancel_requested.clear()
        self.duplicates.clear()
        self._total_scan_start_time = time.monotonic()

        try:
            # --- Phase 1: Build File List ---
            self._reset_phase_progress(0, "Building File List") # Max value unknown initially
            if not quick_scan_if_possible: # Full scan always rebuilds file list
                self._build_file_list()
                if self.on_files_enumerated:
                    self.on_files_enumerated(len(self.db_manager.get_all_paths()))
                self.db_manager.save_database() # Save after building list
            else:
                # For a quick scan, we assume the DB is mostly up-to-date.
                # We might still want a very quick check for new files in include_list
                # or rely entirely on the existing DB. C# version seems to do a full build then compare.
                # For now, quick_scan implies skipping this if DB has entries.
                # This logic needs refinement based on how "quick scan" is truly defined.
                # C# ScanEngine seems to always run BuildFileList and GatherInfos before StartCompare.
                # So, 'quick_scan_if_possible' might mean using cached info more aggressively in GatherInfos.
                # Let's stick to C# pattern: always build/gather.
                logger.info("Quick scan requested, but full file list build and info gathering will occur.")
                self._build_file_list()
                if self.on_files_enumerated:
                    self.on_files_enumerated(len(self.db_manager.get_all_paths()))
                self.db_manager.save_database()


            self._check_pause_cancel()

            # --- Phase 2: Gather Information ---
            # Max value is the number of files we might process from the DB.
            # This count might change based on filters applied at the start of _gather_infos.
            # For now, use total DB entries as a rough max.
            all_db_entries = self.db_manager.get_all_entries()
            self._reset_phase_progress(len(all_db_entries), "Gathering Information")
            self._gather_infos(all_db_entries) # Pass the list to process
            if self.on_info_gathering_complete:
                 # This count should be of successfully processed entries for info.
                processed_count = sum(1 for e in all_db_entries if not e.is_invalid_for_scan and (e.gray_bytes_data or e.flags & EntryFlags.IS_IMAGE))
                self.on_info_gathering_complete(processed_count)
            self.db_manager.save_database() # Save after gathering info

            self._check_pause_cancel()

            # --- Phase 3: Scan for Duplicates ---
            # Max value is the number of valid entries for comparison.
            # This is determined at the start of _scan_for_duplicates.
            # We'll set it inside that method.
            self._reset_phase_progress(0, "Scanning for Duplicates") # Max set later
            self._scan_for_duplicates()

            self._check_pause_cancel()

            # --- Phase 4: Highlight Best Matches (Optional Post-processing) ---
            self._reset_phase_progress(len(self.duplicates), "Highlighting Duplicates")
            self._highlight_best_matches()
            self._report_progress(increment=False) # Final progress update for this phase

            if self.on_scan_complete:
                self.on_scan_complete(self.duplicates)
            logger.info("Scan completed successfully.")

        except OperationCanceledError:
            logger.info("Scan was cancelled.")
            if self.on_scan_aborted:
                self.on_scan_aborted()
        except Exception as e:
            logger.error(f"An error occurred during the scan: {e}", exc_info=True)
            # Optionally, trigger on_scan_aborted or a specific error event
            if self.on_scan_aborted: # Or a new on_scan_error event
                self.on_scan_aborted() # For now, treat unexpected errors as aborts
        finally:
            self._is_scanning = False
            self._is_paused.clear() # Ensure not left in paused state
            # self._cancel_requested.clear() # Keep it set if cancelled, or clear for next run.
                                         # Usually, new CancellationTokenSource is made.

    def _build_file_list(self):
        """
        Scans include directories, filters files, and updates the database.
        """
        logger.info("Building file list...")
        # Phase 1: Count total files to establish a max_value for progress.
        # This initial walk is just for counting.
        logger.info("Building file list: Counting files...")
        total_files_to_potentially_process = 0
        preliminary_file_paths_to_scan: List[Tuple[str, str, str]] = [] # root, dir, filename

        # Initial progress setup for counting phase
        # Using number of include_list paths as a rough measure for this sub-phase.
        self._reset_phase_progress(len(self.settings.include_list), "Building File List (Counting...)")

        media_extensions = get_media_extensions()

        for i, scan_dir_path_orig in enumerate(self.settings.include_list):
            self._check_pause_cancel()
            self._current_phase_progress = i+1
            self._report_progress(current_file=scan_dir_path_orig, increment=False, phase_override="Building File List (Counting...)")

            if not os.path.isdir(scan_dir_path_orig):
                logger.warning(f"Include directory not found: {scan_dir_path_orig}")
                continue

            abs_scan_dir_path = os.path.abspath(scan_dir_path_orig)
            for root, dirs, files in os.walk(abs_scan_dir_path, topdown=True, followlinks=not self.settings.ignore_reparse_points):
                self._check_pause_cancel()
                # Filter directories (blacklist, read-only, reparse points)
                dirs[:] = [d for d in dirs if not any(
                    os.path.join(root, d).startswith(blacklisted_path) for blacklisted_path in self.settings.blacklist
                )]
                if not self.settings.include_subdirectories and root != abs_scan_dir_path:
                    dirs[:] = []
                    if root != abs_scan_dir_path: continue

                for filename in files:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in media_extensions: continue
                    if not self.settings.include_images and is_image_file(os.path.join(root, filename)): continue

                    # Store tuple for second pass to avoid redundant os.path.join
                    preliminary_file_paths_to_scan.append((root, filename, os.path.abspath(os.path.join(root, filename))))
                    total_files_to_potentially_process +=1

        logger.info(f"Found {total_files_to_potentially_process} potential media files to process.")
        self._reset_phase_progress(total_files_to_potentially_process, "Building File List (Processing Files)")

        processed_paths_in_this_scan: Set[str] = set() # Tracks abspaths processed in this scan run

        # Phase 2: Process each identified file.
        for root, filename, file_path in preliminary_file_paths_to_scan:
            self._check_pause_cancel()
            self._report_progress(current_file=filename) # Report progress per file

            # Path is already absolute from preliminary scan
            # if file_path in processed_paths_in_this_scan: continue # Should not happen if preliminary_file_paths_to_scan is unique

            # Blacklist check for files (already somewhat handled by dir filtering, but good for specific file blacklists)
            if any(file_path.startswith(blacklisted_path) for blacklisted_path in self.settings.blacklist):
                logger.debug(f"Skipping blacklisted file: {file_path}")
                continue

            abs_scan_dir_path = os.path.abspath(scan_dir_path)
            logger.info(f"Scanning directory: {abs_scan_dir_path}")

            for root, dirs, files in os.walk(abs_scan_dir_path, topdown=True):
                self._check_pause_cancel()

                # Filter directories (blacklist, read-only, reparse points)
                # Python's os.walk `followlinks=False` (default) avoids reparse points like junctions
                # if not self.settings.ignore_reparse_points for dirs, specific checks needed.
                # For blacklist, dirs[:] can be modified to prune traversal.

                # Example blacklist for dirs (simple prefix match)
                dirs[:] = [d for d in dirs if not any(
                    os.path.join(root, d).startswith(blacklisted_path) for blacklisted_path in self.settings.blacklist
                )]

                if not self.settings.include_subdirectories and root != abs_scan_dir_path:
                    dirs[:] = [] # Don't descend further if not including subdirectories
                    # We still process files in the current root if it's the top scan_dir_path
                    if root != abs_scan_dir_path: continue


                for filename in files:
                    self._check_pause_cancel()
                    file_path = os.path.abspath(os.path.join(root, filename))

                    if file_path in processed_paths_in_this_scan: continue # Avoid reprocessing symlink cycles if followlinks=True
                    processed_paths_in_this_scan.add(file_path)

                    # Blacklist check for files
                    if any(file_path.startswith(blacklisted_path) for blacklisted_path in self.settings.blacklist):
                        logger.debug(f"Skipping blacklisted file: {file_path}")
                        continue

                    # Extension check
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in media_extensions:
                        continue

                    # Image inclusion check
                    if not self.settings.include_images and is_image_file(file_path):
                        continue

                    # File size filter (early check if possible, but FileEntry handles it more reliably)
                    try:
                        stat_info = os.stat(file_path)
                        if self.settings.filter_by_file_size:
                            size_mb = stat_info.st_size / (1024 * 1024)
                            if size_mb < self.settings.minimum_file_size_mb or \
                               size_mb > self.settings.maximum_file_size_mb:
                                logger.debug(f"Skipping due to size filter (early): {file_path} ({size_mb:.2f}MB)")
                                continue
                    except OSError: # File might have disappeared or unreadable
                        logger.warning(f"Could not stat file (early check): {file_path}")
                        continue


                    # Check against database
                    db_entry = self.db_manager.get_entry(file_path)
                    current_mtime = stat_info.st_mtime
                    current_size = stat_info.st_size

                    if db_entry:
                        # File exists in DB. Check if modified.
                        # FileEntry stores UTC datetime, os.stat gives local time usually.
                        # For simplicity, compare raw mtime. For robustness, convert to UTC.
                        # db_entry.date_modified_utc should be datetime.utcfromtimestamp(stat_info.st_mtime)
                        if db_entry.date_modified_utc and \
                           abs(db_entry.date_modified_utc.timestamp() - current_mtime) < 1 and \
                           db_entry.file_size_bytes == current_size:
                            logger.debug(f"File unchanged, skipping add/update: {file_path}")
                            db_entry.is_invalid_for_scan = False # Reset this runtime flag for existing entries
                            continue # Unchanged
                        else:
                            logger.info(f"File changed, re-evaluating: {file_path}")
                            # Mark for re-processing: clear old metadata/thumbnails
                            db_entry.media_info = None
                            db_entry.gray_bytes_data.clear()
                            db_entry.flags = EntryFlags.NONE # Reset flags (except IS_IMAGE potentially)
                            if is_image_file(file_path): db_entry.flags |= EntryFlags.IS_IMAGE
                            # Update stat info
                            db_entry.file_size_bytes = current_size
                            try: db_entry.date_created_utc = datetime.utcfromtimestamp(stat_info.st_ctime)
                            except AttributeError: db_entry.date_created_utc = datetime.utcfromtimestamp(current_mtime)
                            db_entry.date_modified_utc = datetime.utcfromtimestamp(current_mtime)
                            db_entry.is_invalid_for_scan = False
                            self.db_manager.add_or_update_entry(db_entry)
                    else:
                        # New file
                        try:
                            logger.debug(f"New file found: {file_path}")
                            entry = FileEntry(path=file_path) # __post_init__ populates stat
                             # Double check size filter, as FileEntry now has the size
                            if self.settings.filter_by_file_size:
                                size_mb = entry.file_size_bytes / (1024 * 1024)
                                if size_mb < self.settings.minimum_file_size_mb or \
                                   size_mb > self.settings.maximum_file_size_mb:
                                    logger.debug(f"Skipping new file due to size filter: {file_path} ({size_mb:.2f}MB)")
                                    continue
                            self.db_manager.add_or_update_entry(entry)
                        except Exception as e:
                            logger.error(f"Error creating FileEntry for new file {file_path}: {e}")

        # After iterating all include_lists, perform a cleanup based on current DB state vs disk state
        # This is slightly different from C# which does it as a separate user action.
        # Here, we can remove DB entries for files that were in DB but NOT found in this scan AND
        # settings.include_non_existing_files is false.
        if not self.settings.include_non_existing_files:
            db_paths = self.db_manager.get_all_paths()
            paths_to_remove = db_paths - processed_paths_in_this_scan
            if paths_to_remove:
                logger.info(f"Removing {len(paths_to_remove)} entries for files no longer found on disk (and settings disallow non-existing).")
                for p_to_remove in paths_to_remove:
                    self.db_manager.remove_entry(p_to_remove)

        logger.info("File list building complete.")
        # Actual max value for progress reporting for this phase is tricky.
        # Could be set to number of files processed if that's counted.

    def _gather_infos(self, entries_to_process: List[FileEntry]):
        """
        Gathers media information and thumbnails for entries.
        Uses ThreadPoolExecutor for parallelism.
        """
        # The list `entries_to_process` is passed, typically all entries from DB.
        # We filter it here based on settings and need for processing.

        candidate_entries: List[FileEntry] = []
        for entry in entries_to_process:
            self._check_pause_cancel() # Check before starting the main loop

            # Basic filters (already applied in build_file_list for new files, re-check for existing)
            if any(entry.path.startswith(blacklisted_path) for blacklisted_path in self.settings.blacklist):
                entry.is_invalid_for_scan = True; continue
            if not self.settings.include_images and (entry.flags & EntryFlags.IS_IMAGE):
                entry.is_invalid_for_scan = True; continue

            # Path contains/not_contains filters (simple substring match for now)
            path_lower = entry.path.lower()
            if self.settings.filter_by_file_path_contains:
                if not any(text.lower() in path_lower for text in self.settings.file_path_contains_texts):
                    entry.is_invalid_for_scan = True; continue
            if self.settings.filter_by_file_path_not_contains:
                if any(text.lower() in path_lower for text in self.settings.file_path_not_contains_texts):
                    entry.is_invalid_for_scan = True; continue

            # Check if info is needed
            needs_metadata = not entry.media_info and not (entry.flags & EntryFlags.IS_IMAGE)
            needs_thumbnails = not entry.gray_bytes_data
            # Determine if processing is needed
            process_this_entry = False
            if entry.flags & EntryFlags.IS_IMAGE:
                # For images, process if no thumbnail at 0.0, or if retrying errors
                if not entry.gray_bytes_data.get(0.0):
                    process_this_entry = True
                elif self.settings.always_retry_failed_sampling and \
                     (entry.flags & EntryFlags.THUMBNAIL_ERROR or entry.flags & EntryFlags.TOO_DARK):
                    process_this_entry = True
            else: # For videos
                if not entry.media_info: # Always need metadata if missing
                    process_this_entry = True
                elif self.settings.always_retry_failed_sampling and (entry.flags & EntryFlags.METADATA_ERROR):
                    process_this_entry = True # Retry metadata error

                if not self.settings.thumbnail_positions: # No thumbnails to get if no positions defined
                    pass # process_this_entry remains as is from metadata check
                elif len(entry.gray_bytes_data) < len(self.settings.thumbnail_positions):
                    # Not enough thumbnails compared to current settings
                    process_this_entry = True
                elif self.settings.always_retry_failed_sampling and \
                     (entry.flags & EntryFlags.THUMBNAIL_ERROR or entry.flags & EntryFlags.TOO_DARK):
                    process_this_entry = True

                # More advanced: Check if existing thumbnail timestamps match current settings.
                # This is complex. For now, rely on count and always_retry.
                # If thumbnail_positions setting changed, a full re-gather might be needed.
                # A simple heuristic: if settings changed, and not always_retry, maybe force re-check.
                current_thumb_sig = get_thumbnail_config_signature(self.settings.thumbnail_positions)
                if entry.thumb_config_signature != current_thumb_sig:
                    logger.info(f"Thumbnail configuration changed for {entry.path}. Marking for re-processing.")
                    process_this_entry = True
                    # When reprocessing due to config change, ensure old thumbs are cleared if not already by error retry logic
                    if not (self.settings.always_retry_failed_sampling and (entry.flags & EntryFlags.THUMBNAIL_ERROR or entry.flags & EntryFlags.TOO_DARK)):
                        entry.gray_bytes_data.clear()
                        entry.flags &= ~EntryFlags.THUMBNAIL_ERROR # Clear old errors as we are reprocessing
                        entry.flags &= ~EntryFlags.TOO_DARK


            if process_this_entry:
                entry.is_invalid_for_scan = False # Reset for processing attempt
                # Clear relevant error flags if retrying
                if self.settings.always_retry_failed_sampling:
                    if not (entry.flags & EntryFlags.IS_IMAGE) and (entry.flags & EntryFlags.METADATA_ERROR):
                        entry.flags &= ~EntryFlags.METADATA_ERROR
                        entry.media_info = None # Force re-fetch
                    if entry.flags & EntryFlags.THUMBNAIL_ERROR:
                        entry.flags &= ~EntryFlags.THUMBNAIL_ERROR
                        entry.gray_bytes_data.clear() # Clear potentially partial/bad data
                    if entry.flags & EntryFlags.TOO_DARK:
                        entry.flags &= ~EntryFlags.TOO_DARK
                        # gray_bytes_data might still be valid but dark, clear if THUMBNAIL_ERROR was also set.
                        # If only TOO_DARK, retry might just confirm it's still dark.
                        # For simplicity, if retrying thumbnail stage, clear gray_bytes.
                        if not (entry.flags & EntryFlags.THUMBNAIL_ERROR): # if it was just too_dark but not error
                             entry.gray_bytes_data.clear()


                candidate_entries.append(entry)
            else:
                # Ensure runtime flag is correctly set if no processing needed but entry is valid
                if not (entry.flags & EntryFlags.METADATA_ERROR) and \
                   not (entry.flags & EntryFlags.THUMBNAIL_ERROR and not entry.gray_bytes_data):
                   entry.is_invalid_for_scan = False
                else: # Has persistent errors and not retrying, so it's invalid for scan
                   entry.is_invalid_for_scan = True



        if not candidate_entries:
            logger.info("No files require information gathering.")
            self._reset_phase_progress(0, "Gathering Information") # Ensure progress bar shows 0/0 or completes
            self._report_progress(increment=False)
            return

        self._reset_phase_progress(len(candidate_entries), "Gathering Information")

        # Use ThreadPoolExecutor for parallel processing
        # FFmpeg calls can be I/O bound (disk read) and CPU bound (decode/encode)
        # ThreadPool might be okay due to I/O, ProcessPool if heavily CPU bound and GIL is an issue.
        # Start with ThreadPool. Max workers from settings.
        max_workers = max(1, self.settings.max_degree_of_parallelism)
        logger.info(f"Gathering info using up to {max_workers} worker threads...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._process_single_entry_info, entry): entry for entry in candidate_entries}
            for future in as_completed(futures):
                self._check_pause_cancel()
                processed_entry = futures[future]
                try:
                    future.result() # Raise exceptions if any occurred in the thread
                    logger.debug(f"Successfully gathered info for: {processed_entry.path}")
                except OperationCanceledError: # Propagate if caught from _check_pause_cancel in thread
                    raise
                except Exception as e:
                    logger.error(f"Error gathering info for {processed_entry.path}: {e}", exc_info=self.settings.extended_ffmpeg_logging)
                    # The entry itself should have flags set by _process_single_entry_info

                self._report_progress(current_file=processed_entry.path)

        logger.info("Information gathering complete.")


    def _process_single_entry_info(self, entry: FileEntry):
        """Worker function to get metadata and thumbnails for a single entry."""
        self._check_pause_cancel() # Check at start of task

        # Get MediaInfo
        if not entry.media_info and not (entry.flags & EntryFlags.IS_IMAGE):
            if entry.flags & EntryFlags.METADATA_ERROR and self.settings.always_retry_failed_sampling:
                entry.flags &= ~EntryFlags.METADATA_ERROR # Clear for retry

            if not (entry.flags & EntryFlags.METADATA_ERROR): # Don't retry if still flagged and not always_retry
                logger.debug(f"Fetching metadata for: {entry.path}")
                entry.media_info = get_media_info(entry.path, self.settings)
                if not entry.media_info:
                    entry.flags |= EntryFlags.METADATA_ERROR
                    entry.is_invalid_for_scan = True # Cannot proceed without metadata for videos
                    logger.warning(f"Failed to get metadata for: {entry.path}")
                    return # Stop processing this entry if metadata fails

        self._check_pause_cancel()

        # Extract gray_bytes
        needs_thumbnails = True
        if entry.flags & EntryFlags.THUMBNAIL_ERROR and self.settings.always_retry_failed_sampling:
            entry.flags &= ~EntryFlags.THUMBNAIL_ERROR # Clear for retry
            entry.flags &= ~EntryFlags.TOO_DARK
            entry.gray_bytes_data.clear() # Clear old failed data
        elif entry.gray_bytes_data: # Already has some thumbnail data
            if entry.flags & EntryFlags.IS_IMAGE and entry.gray_bytes_data.get(0.0):
                needs_thumbnails = False
            elif not (entry.flags & EntryFlags.IS_IMAGE) and self.settings.thumbnail_positions and \
                 len(entry.gray_bytes_data) >= len(self.settings.thumbnail_positions):
                needs_thumbnails = False

        if not (entry.flags & EntryFlags.IS_IMAGE) and not self.settings.thumbnail_positions:
            needs_thumbnails = False # Video but no positions defined.

        if needs_thumbnails and not (entry.flags & EntryFlags.THUMBNAIL_ERROR and not self.settings.always_retry_failed_sampling):
            logger.debug(f"Extracting thumbnails for: {entry.path}")
            success = extract_gray_bytes_for_file_entry(entry, self.settings)
            if not success: # This means a critical error happened during extraction call itself
                logger.warning(f"Thumbnail extraction failed for: {entry.path}")
                # Flags (THUMBNAIL_ERROR, TOO_DARK) are set within extract_gray_bytes
            if not entry.gray_bytes_data and not (entry.flags & EntryFlags.TOO_DARK):
                 # If no thumbnails were produced and it's not because they were all too dark
                 entry.flags |= EntryFlags.THUMBNAIL_ERROR # Ensure it's set

        # If thumbnails were processed and no new errors, update the signature
        if needs_thumbnails and not (entry.flags & EntryFlags.THUMBNAIL_ERROR and not entry.gray_bytes_data):
             # If it's an image, or a video and thumbnail positions are defined
            if (entry.flags & EntryFlags.IS_IMAGE) or self.settings.thumbnail_positions:
                entry.thumb_config_signature = get_thumbnail_config_signature(self.settings.thumbnail_positions if not (entry.flags & EntryFlags.IS_IMAGE) else [])
            else: # Video with no thumbnail positions defined
                entry.thumb_config_signature = get_thumbnail_config_signature([])


        # Final check for scan validity after processing
        if entry.flags & EntryFlags.METADATA_ERROR: entry.is_invalid_for_scan = True
        if entry.flags & EntryFlags.THUMBNAIL_ERROR and not entry.gray_bytes_data : entry.is_invalid_for_scan = True
        # if entry.flags & EntryFlags.TOO_DARK: entry.is_invalid_for_scan = True # Optional: make this a setting


    def _scan_for_duplicates(self):
        """
        Compares entries to find duplicates.
        """
        valid_entries: List[FileEntry] = []
        for entry in self.db_manager.get_all_entries():
            if entry.is_invalid_for_scan: continue
            if entry.flags & EntryFlags.METADATA_ERROR: continue
            if entry.flags & EntryFlags.THUMBNAIL_ERROR and not entry.gray_bytes_data: continue # Error and no data
            if entry.flags & EntryFlags.TOO_DARK and not self.settings.compare_horizontally_flipped: # If all thumbs are dark, usually no comparison
                # This rule might need refinement. C# excludes if TOO_DARK.
                # For now, let's include them if compare_horizontally_flipped is on, as flipping might change dark status (unlikely for uniform dark).
                # Or, more simply, if TOO_DARK, it's often not comparable.
                # Let's assume for now TOO_DARK entries are not compared unless a specific setting allows it.
                # For this initial pass, we'll exclude them from comparison.
                logger.debug(f"Skipping comparison for TOO_DARK entry: {entry.path}")
                continue

            if not (entry.flags & EntryFlags.IS_IMAGE) and not entry.gray_bytes_data and self.settings.thumbnail_positions:
                continue # Video expecting thumbnails but has none
            if (entry.flags & EntryFlags.IS_IMAGE) and not entry.gray_bytes_data.get(0.0):
                continue # Image expecting a thumbnail but has none

            valid_entries.append(entry)

        if not valid_entries or len(valid_entries) < 2:
            logger.info("Not enough valid files to perform duplicate comparison.")
            self._reset_phase_progress(0, "Scanning for Duplicates")
            self._report_progress(increment=False)
            return

        self._reset_phase_progress(len(valid_entries), "Scanning for Duplicates")
        logger.info(f"Comparing {len(valid_entries)} files for duplicates...")

        # Path-based tracking for duplicate groups
        # file_to_group_id: Dict[str, str] = {} # path -> group_id
        # group_id_counter = 0

        # Store identified duplicate pairs: List of (FileEntry1, FileEntry2, ComparisonOutcome)
        identified_duplicate_pairs: List[Tuple[FileEntry, FileEntry, ComparisonOutcome]] = []

        max_workers = max(1, self.settings.max_degree_of_parallelism if self.settings.max_degree_of_parallelism <= 4 else 4)

        comparison_tasks = []
        for i in range(len(valid_entries)):
            for j in range(i + 1, len(valid_entries)):
                comparison_tasks.append((valid_entries[i], valid_entries[j]))

        self._reset_phase_progress(len(comparison_tasks), "Scanning for Duplicates")

        if not comparison_tasks:
             logger.info("No comparison tasks to process.")
             self._report_progress(increment=False) # Final update for this phase
             return

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all comparison tasks
            # Future -> (FileEntry1, FileEntry2)
            future_to_pair: Dict[Any, Tuple[FileEntry, FileEntry]] = {
                executor.submit(check_if_entries_are_duplicate, entry1, entry2, self.settings): (entry1, entry2)
                for entry1, entry2 in comparison_tasks
            }

            for future in as_completed(future_to_pair):
                self._check_pause_cancel()
                entry1, entry2 = future_to_pair[future]

                try:
                    outcome: Optional[ComparisonOutcome] = future.result()
                    if outcome:
                        similarity, was_flipped = outcome
                        logger.info(f"DUPLICATE PAIR: '{entry1.filename}' and '{entry2.filename}' (Similarity: {similarity*100:.2f}%, Flipped: {was_flipped})")
                        identified_duplicate_pairs.append((entry1, entry2, outcome))
                except OperationCanceledError:
                    raise # Propagate cancellation
                except Exception as e:
                    logger.error(f"Error comparing {entry1.path} and {entry2.path}: {e}", exc_info=self.settings.extended_ffmpeg_logging)
                finally:
                    self._report_progress(current_file=f"{entry1.filename} vs {entry2.filename}")

        # --- Grouping Phase (single-threaded) ---
        logger.info(f"Found {len(identified_duplicate_pairs)} duplicate pairs. Now grouping them...")
        self._build_duplicate_groups(identified_duplicate_pairs)

        logger.info(f"Duplicate scanning phase complete. Found {len(self.duplicates)} duplicate groups.")


    def _build_duplicate_groups(self, duplicate_pairs: List[Tuple[FileEntry, FileEntry, ComparisonOutcome]]):
        """
        Builds final duplicate groups from a list of identified duplicate pairs.
        Uses a Disjoint Set Union (DSU) like approach for grouping.
        """
        self.duplicates.clear()

        # Map path to FileEntry object for quick lookup
        path_to_entry: Dict[str, FileEntry] = {entry.path: entry for pair in duplicate_pairs for entry in (pair[0], pair[1])}

        # DSU structure: parent maps each item (path) to its representative parent
        parent: Dict[str, str] = {path: path for path in path_to_entry.keys()}
        # Store associated data with the representative (e.g. first outcome for the pair that formed part of this group)
        # This part might need more refinement if per-item similarity within a group is needed.
        # For now, DuplicateItemGroup just holds items.

        def find_set(item_path: str) -> str:
            if parent[item_path] == item_path:
                return item_path
            parent[item_path] = find_set(parent[item_path]) # Path compression
            return parent[item_path]

        def unite_sets(path1: str, path2: str):
            root1 = find_set(path1)
            root2 = find_set(path2)
            if root1 != root2:
                parent[root2] = root1 # Simple union: make root1 parent of root2

        for entry1, entry2, outcome in duplicate_pairs:
            # Ensure entries are in path_to_entry, should always be true if list is from comparisons
            if entry1.path not in parent or entry2.path not in parent:
                logger.warning(f"Entries from duplicate pair ({entry1.path}, {entry2.path}) not in initial DSU set. Skipping.")
                continue
            unite_sets(entry1.path, entry2.path)

        # Collect groups
        # temp_groups: group_representative_path -> List[FileEntry]
        temp_groups: Dict[str, List[FileEntry]] = {}
        for path in parent:
            root = find_set(path) # Find the final representative for this path
            entry_obj = path_to_entry[path]
            if root not in temp_groups:
                temp_groups[root] = []
            # Add entry only if not already present (by path, DSU handles set nature)
            if not any(e.path == entry_obj.path for e in temp_groups[root]):
                 temp_groups[root].append(entry_obj)

        # Convert to final DuplicateItemGroup structure
        group_id_counter = 0
        for root_path, items_list in temp_groups.items():
            if len(items_list) > 1: # Only consider actual groups (more than 1 item)
                group_id_str = f"group_{group_id_counter}"
                group_id_counter += 1

                new_group = DuplicateItemGroup(group_id_str)
                # Find one of the original pairs that belongs to this group to get a representative similarity
                # This is a simplification. A more robust approach would store similarity for each item
                # relative to a reference, or the max similarity that linked it.
                representative_similarity = 1.0 # Default if no pair found (should not happen for groups > 1)

                # Try to find a pair that involves an item from this group to get a similarity
                # This is still not perfect as it picks one arbitrary similarity.
                first_item_path_in_group = items_list[0].path
                found_similarity_for_group = False
                for e1, e2, outcome in duplicate_pairs:
                    if e1.path == first_item_path_in_group or e2.path == first_item_path_in_group:
                        # Check if both e1 and e2 ended up in this same group
                        if find_set(e1.path) == root_path and find_set(e2.path) == root_path:
                            representative_similarity = outcome[0] # outcome is (similarity, was_flipped)
                            found_similarity_for_group = True
                            break
                if not found_similarity_for_group and len(items_list) > 1:
                     # Fallback if the above logic didn't find a specific pair for the group's first item.
                     # This can happen if the first item was linked transitively.
                     # Just find any pair that contributed to this merged group.
                    for e1, e2, outcome in duplicate_pairs:
                        if find_set(e1.path) == root_path and find_set(e2.path) == root_path:
                            representative_similarity = outcome[0]
                            break


                for item_entry in items_list:
                    # For now, all items in a group get the same representative similarity.
                    # This needs refinement if per-item similarity display is crucial and varied.
                    new_group.add_item(item_entry, similarity=representative_similarity)

                if new_group.items:
                    self.duplicates[group_id_str] = new_group


    def _highlight_best_matches(self):
        """
        Analyzes duplicate groups to flag 'best' items based on criteria like
        resolution, size, duration, etc. (Mirrors C# ScanEngine logic).
        This populates DuplicateItemGroup.best_match_flags or similar.
        """
        if not self.duplicates:
            self._report_progress(increment=False) # Complete phase if no duplicates
            return

        logger.info("Highlighting best matches in duplicate groups...")

        for group_id, group in self.duplicates.items():
            self._check_pause_cancel()
            if not group.items: continue
            group.best_match_flags.clear() # Clear previous flags

            # Helper to add flag
            def add_best_flag(item_path: str, flag_name: str):
                if item_path not in group.best_match_flags:
                    group.best_match_flags[item_path] = set()
                group.best_match_flags[item_path].add(flag_name)

            # --- Duration (for videos) ---
            if not (group.items[0].flags & EntryFlags.IS_IMAGE):
                sorted_by_duration = sorted(
                    [item for item in group.items if item.media_info and item.media_info.duration_seconds is not None],
                    key=lambda e: e.media_info.duration_seconds,
                    reverse=True
                )
                if sorted_by_duration:
                    best_duration = sorted_by_duration[0].media_info.duration_seconds
                    for item in sorted_by_duration:
                        if item.media_info and item.media_info.duration_seconds == best_duration:
                            add_best_flag(item.path, "BestDuration")
                            logger.debug(f"Group {group_id}: Best Duration - {item.filename}")
                        else: break

            # --- Smallest File Size (IsBestSize in C# seems to be smallest) ---
            sorted_by_file_size = sorted(group.items, key=lambda e: e.file_size_bytes)
            if sorted_by_file_size:
                smallest_size = sorted_by_file_size[0].file_size_bytes
                for item in sorted_by_file_size:
                    if item.file_size_bytes == smallest_size:
                        add_best_flag(item.path, "BestSize") # C# calls it BestSize for smallest
                        logger.debug(f"Group {group_id}: Smallest File Size (BestSize) - {item.filename}")
                    else: break

            # --- Largest Frame Size (Resolution - IsBestFrameSize) ---
            def get_frame_size(e: FileEntry):
                if e.media_info and e.media_info.width and e.media_info.height:
                    return e.media_info.width * e.media_info.height
                return 0

            sorted_by_resolution = sorted(
                [item for item in group.items if get_frame_size(item) > 0],
                key=get_frame_size,
                reverse=True
            )
            if sorted_by_resolution:
                best_res_val = get_frame_size(sorted_by_resolution[0])
                for item in sorted_by_resolution:
                    if get_frame_size(item) == best_res_val:
                        add_best_flag(item.path, "BestFrameSize")
                        logger.debug(f"Group {group_id}: Best Resolution - {item.filename} ({item.media_info.width}x{item.media_info.height if item.media_info else ''})")
                    else: break

            # --- Highest FPS (IsBestFps - for videos) ---
            if not (group.items[0].flags & EntryFlags.IS_IMAGE):
                def get_fps(e: FileEntry):
                    if e.media_info and e.media_info.primary_video_stream and e.media_info.primary_video_stream.frame_rate_avg:
                        return e.media_info.primary_video_stream.frame_rate_avg
                    return 0.0

                sorted_by_fps = sorted(
                    [item for item in group.items if get_fps(item) > 0],
                    key=get_fps,
                    reverse=True
                )
                if sorted_by_fps:
                    best_fps_val = get_fps(sorted_by_fps[0])
                    for item in sorted_by_fps:
                        if get_fps(item) == best_fps_val:
                            add_best_flag(item.path, "BestFps")
                            logger.debug(f"Group {group_id}: Best FPS - {item.filename}")
                        else: break

            # --- Highest Bitrate (IsBestBitRateKbs - for videos) ---
            if not (group.items[0].flags & EntryFlags.IS_IMAGE):
                def get_bitrate(e: FileEntry): # Overall bitrate or primary video stream
                    if e.media_info:
                        if e.media_info.bit_rate_bps: return e.media_info.bit_rate_bps
                        pvs = e.media_info.primary_video_stream
                        if pvs and pvs.bit_rate: return pvs.bit_rate
                    return 0

                sorted_by_bitrate = sorted(
                     [item for item in group.items if get_bitrate(item) > 0],
                    key=get_bitrate,
                    reverse=True
                )
                if sorted_by_bitrate:
                    best_bitrate_val = get_bitrate(sorted_by_bitrate[0])
                    for item in sorted_by_bitrate:
                        if get_bitrate(item) == best_bitrate_val:
                            add_best_flag(item.path, "BestBitRate")
                            logger.debug(f"Group {group_id}: Best Bitrate - {item.filename}")
                        else: break

            # TODO: Add AudioSampleRate if needed

            self._report_progress(current_file=f"Group {group_id}")
        logger.info("Highlighting complete.")


    def pause_scan(self):
        if self._is_scanning and not self._is_paused.is_set():
            self._is_paused.set()
            logger.info("Scan paused.")

    def resume_scan(self):
        if self._is_scanning and self._is_paused.is_set():
            self._is_paused.clear()
            logger.info("Scan resumed.")

    def stop_scan(self):
        if self._is_scanning:
            self._cancel_requested.set()
            if self._is_paused.is_set(): # If paused, need to release it to see cancel flag
                self._is_paused.clear()
            logger.info("Scan stop requested.")

    # --- Other utility methods from C# ScanEngine ---
    def cleanup_database_interactive(self): # Name change to reflect it uses current DB state
        """Performs cleanup on the current DB contents, then saves."""
        logger.info("Starting database cleanup...")
        # In C#, this checks File.Exists for each entry.
        # We can get all current paths and pass it to db_manager.cleanup_database
        # or let db_manager handle os.path.exists if no set is passed.
        # For more control, ScanEngine can provide the set of existing files from a quick disk re-check.

        # For simplicity now, assume db_manager.cleanup_database can do its job.
        # A more thorough cleanup might re-walk include_list to get current disk state.
        # For now, let's make it simple:
        all_db_paths = self.db_manager.get_all_paths()
        existing_on_disk: Set[str] = set()
        if not self.settings.include_non_existing_files: # Only check disk if this setting is active
            logger.info("Verifying existence of database files on disk for cleanup...")
            # This could be slow for large DBs.
            # A quick re-walk of include_list might be better than statting every DB entry.
            # For now, direct check:
            for p in all_db_paths:
                if os.path.exists(p):
                    existing_on_disk.add(p)
            self.db_manager.cleanup_database(existing_on_disk)
        else:
            self.db_manager.cleanup_database(None) # Cleanup based on flags only

        self.db_manager.save_database()
        logger.info("Database cleanup complete and saved.")

    def get_broken_file_entries(self) -> List[FileEntry]:
        broken: List[FileEntry] = []
        for entry in self.db_manager.get_all_entries():
            if entry.flags & EntryFlags.METADATA_ERROR or \
               (entry.flags & EntryFlags.THUMBNAIL_ERROR and not entry.gray_bytes_data):
                broken.append(entry)
        return broken

    # clear_database, blacklist_file_entry, update_file_path can be direct calls to db_manager
    # if ScanEngine doesn't need to add extra logic around them.

class OperationCanceledError(Exception):
    """Custom exception for when an operation is cancelled."""
    pass

```

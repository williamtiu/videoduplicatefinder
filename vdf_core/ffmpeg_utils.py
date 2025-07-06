"""
FFmpeg and FFprobe utilities for VideoDuplicateFinder Python
using the ffmpeg-python library.
"""
import ffmpeg # type: ignore
import json
import logging # Import logging
from typing import Optional, List, Dict, Tuple
import os

from .settings import CoreSettings, ThumbnailPositionSetting, ThumbnailPositionType, HardwareAcceleration
from .file_entry import FileEntry, MediaInfo, StreamInfo, EntryFlags # Assuming file_entry.py is in the same package

# Helper to convert hh:mm:ss.xx string to seconds
def _time_str_to_seconds(time_str: Optional[str]) -> Optional[float]:
    if not time_str:
        return None
    parts = time_str.split(':')
    try:
        if len(parts) == 3:
            h, m, s_ms = parts
            s_parts = s_ms.split('.')
            s = int(s_parts[0])
            ms = int(s_parts[1]) if len(s_parts) > 1 else 0
            return int(h) * 3600 + int(m) * 60 + s + ms / 1000.0
        elif len(parts) == 2: # mm:ss.xx
            m, s_ms = parts
            s_parts = s_ms.split('.')
            s = int(s_parts[0])
            ms = int(s_parts[1]) if len(s_parts) > 1 else 0
            return int(m) * 60 + s + ms / 1000.0
        elif len(parts) == 1: # ss.xx
             s_parts = parts[0].split('.')
             s = int(s_parts[0])
             ms = int(s_parts[1]) if len(s_parts) > 1 else 0
             return s + ms / 1000.0
    except ValueError:
        return None # Invalid format
    return None


def get_media_info(file_path: str, settings: CoreSettings) -> Optional[MediaInfo]:
    """
    Extracts media information from a file using ffprobe.
    Returns a MediaInfo object or None if an error occurs.
    """
    if not os.path.exists(file_path):
        # print(f"Error: File not found at {file_path}")
        return None

    try:
        probe_opts = {"show_format": None, "show_streams": None}
        if settings.extended_ffmpeg_logging:
            probe_opts["loglevel"] = "error" # or "info", "debug"

        # Add custom arguments if any (though ffprobe has fewer options here)
        # For ffprobe, custom arguments are typically less about performance and more about what to show.
        # ffmpeg-python's probe() doesn't have a direct `global_args` or `kwargs` for arbitrary ffprobe flags beyond cmd.
        # However, we can pass `cmd` to specify ffprobe executable if needed, or other specific options.
        # For now, relying on standard probe_opts. If specific ffprobe custom args are needed,
        # it might require constructing the command line and running ffprobe as a subprocess directly.
        # For common things like loglevel, it's handled by probe_opts.

        probe = ffmpeg.probe(file_path, **probe_opts)

        # ---- Populate MediaInfo from probe data ----
        mi = MediaInfo()

        # Format information
        if 'format' in probe:
            fmt = probe['format']
            mi.format_name = fmt.get('format_name')
            mi.format_long_name = fmt.get('format_long_name')
            mi.duration_seconds = float(fmt.get('duration', 0.0)) if fmt.get('duration') else None
            mi.size_bytes = int(fmt.get('size', 0)) if fmt.get('size') else None
            mi.bit_rate_bps = int(fmt.get('bit_rate', 0)) if fmt.get('bit_rate') else None

        # Stream information
        if 'streams' in probe:
            for s_data in probe['streams']:
                si = StreamInfo(index=int(s_data.get('index', -1)))
                si.codec_type = s_data.get('codec_type')
                si.codec_name = s_data.get('codec_name')

                if si.codec_type == 'video':
                    si.width = int(s_data.get('width', 0)) if s_data.get('width') else None
                    si.height = int(s_data.get('height', 0)) if s_data.get('height') else None
                    si.pix_fmt = s_data.get('pix_format')

                    avg_fps_str = s_data.get('avg_frame_rate')
                    if avg_fps_str and '/' in avg_fps_str:
                        num, den = map(int, avg_fps_str.split('/'))
                        if den != 0:
                            si.frame_rate_avg = num / den

                    # Fallback or alternative for frame rate if avg_frame_rate is "0/0" or missing
                    if not si.frame_rate_avg or si.frame_rate_avg == 0:
                        r_fps_str = s_data.get('r_frame_rate')
                        if r_fps_str and '/' in r_fps_str:
                            num, den = map(int, r_fps_str.split('/'))
                            if den != 0:
                                si.frame_rate_avg = num / den

                elif si.codec_type == 'audio':
                    si.sample_rate = int(s_data.get('sample_rate', 0)) if s_data.get('sample_rate') else None
                    si.channels = int(s_data.get('channels', 0)) if s_data.get('channels') else None
                    si.channel_layout = s_data.get('channel_layout')

                common_duration_str = s_data.get('duration')
                if common_duration_str:
                    try:
                        si.duration = float(common_duration_str)
                    except ValueError:
                        pass # Could be N/A or other string

                common_bit_rate_str = s_data.get('bit_rate')
                if common_bit_rate_str:
                    try:
                        si.bit_rate = int(common_bit_rate_str)
                    except ValueError:
                        pass # Could be N/A

                mi.streams.append(si)

        # If overall duration is missing from format, try to get it from the longest stream
        if mi.duration_seconds is None or mi.duration_seconds == 0:
            max_stream_duration = 0.0
            for s in mi.streams:
                if s.duration and s.duration > max_stream_duration:
                    max_stream_duration = s.duration
            if max_stream_duration > 0:
                mi.duration_seconds = max_stream_duration

        return mi

    except ffmpeg.Error as e:
        # Log the error if extended logging is on
        if settings.extended_ffmpeg_logging:
            error_message = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "Unknown ffprobe error"
            logger.error(f"FFprobe failed for {file_path}. Error: {error_message}", exc_info=True)
        else:
            logger.warning(f"FFprobe failed for {file_path}. Enable extended FFmpeg logging for details.")
        return None
    except Exception as e:
        # Catch any other unexpected error during probing
        logger.error(f"Unexpected error probing {file_path}: {str(e)}", exc_info=True)
        return None


def _calculate_thumbnail_timestamps_seconds(
    duration_seconds: float,
    position_settings: List[ThumbnailPositionSetting]
) -> List[float]:
    """
    Calculates the precise timestamps in seconds for thumbnail extraction based on settings.
    """
    if duration_seconds <= 0:
        return []

    timestamps: List[float] = []
    for pos_setting in position_settings:
        ts = 0.0
        if pos_setting.type == ThumbnailPositionType.PERCENTAGE:
            ts = duration_seconds * (pos_setting.value / 100.0)
        elif pos_setting.type == ThumbnailPositionType.OFFSET_FROM_START:
            ts = pos_setting.value
        elif pos_setting.type == ThumbnailPositionType.OFFSET_FROM_END:
            ts = duration_seconds - pos_setting.value

        # Clamp timestamp to be within [0, duration_seconds]
        ts = max(0.0, min(ts, duration_seconds))
        timestamps.append(ts)

    # Return unique, sorted timestamps
    return sorted(list(set(timestamps)))


def extract_gray_bytes_for_file_entry(file_entry: FileEntry, settings: CoreSettings) -> bool:
    """
    Extracts 16x16 grayscale thumbnails for a FileEntry based on settings.
    Populates file_entry.gray_bytes_data.
    Returns True if successful (even if some thumbnails fail, as long as not all do or metadata is fine),
    False if a critical error occurs (e.g., file not found, no duration for video).
    """
    if not os.path.exists(file_entry.path):
        file_entry.flags |= EntryFlags.METADATA_ERROR # Or a more specific FILE_NOT_FOUND
        return False

    if file_entry.flags & EntryFlags.IS_IMAGE:
        # Handle image: one "thumbnail" at timestamp 0.0
        try:
            # ffmpeg command to convert image to 16x16 grayscale PNG, then read bytes
            # Using PNG intermediate to ensure consistent format before getting raw bytes.
            # vf options: format=gray, scale=16:16
            # Using .output('pipe:', format='rawvideo', pix_fmt='gray', s='16x16')
            # Or, use Pillow for images if already a dependency for other things.
            # For consistency with C# (which uses ffmpeg for images too for graybytes):

            ffmpeg_input_args = {}
            ffmpeg_output_args = {
                'format': 'rawvideo',
                'pix_fmt': 'gray',
                's': '16x16', # widthxheight
                'vframes': 1
            }
            if settings.custom_ffmpeg_arguments:
                 # This is tricky as custom args might conflict.
                 # For raw output, usually no complex custom args.
                 pass


            # Determine global arguments (e.g., -hide_banner, loglevel)
            global_args_list = ['-hide_banner']
            if settings.extended_ffmpeg_logging:
                global_args_list.extend(['-loglevel', 'error']) # or info/debug

            # Add custom ffmpeg arguments if specified
            if settings.custom_ffmpeg_arguments:
                # Assuming custom_ffmpeg_arguments is a string of space-separated options
                global_args_list.extend(settings.custom_ffmpeg_arguments.split())


            process = (
                ffmpeg
                .input(file_entry.path, **ffmpeg_input_args)
                .output('pipe:', **ffmpeg_output_args)
                .global_args(*global_args_list)
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
            out_bytes, err_bytes = process.communicate()

            if process.returncode != 0:
                file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
                err_msg = err_bytes.decode('utf-8', errors='ignore')
                if settings.extended_ffmpeg_logging:
                    logger.error(f"FFmpeg failed for image {file_entry.path}. Error: {err_msg}", exc_info=True)
                else:
                    logger.warning(f"FFmpeg failed for image {file_entry.path}. Enable extended FFmpeg logging for details.")
                return False

            if len(out_bytes) == 16 * 16:
                file_entry.gray_bytes_data[0.0] = out_bytes
                if are_gray_bytes_too_dark(out_bytes):
                    file_entry.flags |= EntryFlags.TOO_DARK
                    logger.debug(f"Image thumbnail marked as TOO_DARK: {file_entry.path}")
                return True
            else:
                file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
                logger.warning(f"FFmpeg output for image {file_entry.path} was unexpected size: {len(out_bytes)} bytes.")
                return False

        except ffmpeg.Error as e:
            file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
            if settings.extended_ffmpeg_logging:
                logger.error(f"FFmpeg error processing image {file_entry.path}: {e.stderr.decode('utf-8', errors='ignore')}", exc_info=True)
            else:
                logger.warning(f"FFmpeg error processing image {file_entry.path}. Enable extended FFmpeg logging for details.")
            return False
        except Exception as e:
            file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
            logger.error(f"Unexpected error processing image {file_entry.path}: {str(e)}", exc_info=True)
            return False

    # Handle Video
    if not file_entry.media_info or not file_entry.media_info.duration_seconds or file_entry.media_info.duration_seconds <= 0:
        # Try to get media info if missing
        if not file_entry.media_info:
            file_entry.media_info = get_media_info(file_entry.path, settings)

        if not file_entry.media_info or not file_entry.media_info.duration_seconds or file_entry.media_info.duration_seconds <= 0:
            file_entry.flags |= EntryFlags.METADATA_ERROR
            return False # Cannot process video without duration

    timestamps_to_extract = _calculate_thumbnail_timestamps_seconds(
        file_entry.media_info.duration_seconds,
        settings.thumbnail_positions
    )

    if not timestamps_to_extract: # No positions defined or zero duration
        if settings.thumbnail_positions: # Positions were defined, but couldn't generate timestamps
             file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
        return True # Technically not an error if no thumbnails were requested

    successful_extractions = 0
    for ts_seconds in timestamps_to_extract:
        # Skip if already processed (e.g. from DB) and not forced retry
        if ts_seconds in file_entry.gray_bytes_data and \
           file_entry.gray_bytes_data[ts_seconds] is not None and \
           not settings.always_retry_failed_sampling:
            successful_extractions +=1
            continue
        if ts_seconds in file_entry.gray_bytes_data and \
           file_entry.gray_bytes_data[ts_seconds] is None and \
           not settings.always_retry_failed_sampling and \
           not (file_entry.flags & EntryFlags.THUMBNAIL_ERROR): # If it failed before, but not marked as general error
             continue # Skip previously failed specific thumbnail unless retrying all


        try:
            ffmpeg_input_args = {'ss': ts_seconds} # Seek to position
            # Add hwaccel if specified and not auto/none
            # For `ffmpeg-python`, hwaccel is typically an input option.
            # Some hwaccels might need `hwaccel_output_format` to bring frames to system memory
            # before converting to 'gray'. This can be complex.
            # Example: .input(..., hwaccel='cuda', hwaccel_output_format='cuda') then filter to map to CPU.
            # For simplicity here, if hwaccel is used, we assume it can output to CPU for gray conversion,
            # or that ffmpeg handles it. More advanced hwaccel pipelines are possible but add complexity.
            if settings.hardware_acceleration_mode not in [HardwareAcceleration.NONE, HardwareAcceleration.AUTO]:
                # Basic hwaccel application. Might need adjustment for specific hwaccels.
                ffmpeg_input_args['hwaccel'] = str(settings.hardware_acceleration_mode.value)
                # If the hwaccel produces frames not directly usable by 'pix_fmt gray',
                # a `hwaccel_output_format` and possibly a `vf` filter for format conversion
                # (e.g., `hwupload_cuda`, `scale_cuda`, `hwdownload`, `format=gray`) would be needed.
                # This simplified version assumes ffmpeg can handle the direct conversion or the hwaccel output is CPU-compatible.


            ffmpeg_output_args = {
                'format': 'rawvideo',
                'pix_fmt': 'gray',
                's': '16x16',
                'vframes': 1, # Extract one frame
            }

            global_args_list = ['-hide_banner']
            if settings.extended_ffmpeg_logging:
                global_args_list.extend(['-loglevel', 'error'])

            if settings.custom_ffmpeg_arguments:
                global_args_list.extend(settings.custom_ffmpeg_arguments.split())


            process = (
                ffmpeg
                .input(file_entry.path, **ffmpeg_input_args)
                .output('pipe:', **ffmpeg_output_args)
                .global_args(*global_args_list)
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
            out_bytes, err_bytes = process.communicate() # Add timeout?

            if process.returncode != 0:
                file_entry.gray_bytes_data[ts_seconds] = None
                file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
                err_msg = err_bytes.decode('utf-8', errors='ignore')
                if settings.extended_ffmpeg_logging:
                    logger.error(f"FFmpeg failed for video {file_entry.path} at {ts_seconds}s. Error: {err_msg}", exc_info=True)
                else:
                    logger.warning(f"FFmpeg failed for video {file_entry.path} at {ts_seconds}s. Enable extended FFmpeg logging for details.")
            elif len(out_bytes) == 16 * 16:
                file_entry.gray_bytes_data[ts_seconds] = out_bytes
                successful_extractions += 1
                # Individual "too dark" check for this specific thumbnail is done when evaluating EntryFlags.TOO_DARK collectively later
            else:
                file_entry.gray_bytes_data[ts_seconds] = None
                file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
                logger.warning(f"FFmpeg output for video {file_entry.path} at {ts_seconds}s was unexpected size: {len(out_bytes)} bytes.")

        except ffmpeg.Error as e:
            file_entry.gray_bytes_data[ts_seconds] = None
            file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
            if settings.extended_ffmpeg_logging:
                logger.error(f"FFmpeg error processing video {file_entry.path} at {ts_seconds}s: {e.stderr.decode('utf-8', errors='ignore')}", exc_info=True)
            else:
                logger.warning(f"FFmpeg error processing video {file_entry.path} at {ts_seconds}s. Enable extended FFmpeg logging for details.")
        except Exception as e:
            file_entry.gray_bytes_data[ts_seconds] = None
            file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
            logger.error(f"Unexpected error processing video {file_entry.path} at {ts_seconds}s: {str(e)}", exc_info=True)

    if not successful_extractions and timestamps_to_extract:
        logger.warning(f"All thumbnail extractions failed for video: {file_entry.path}")
        file_entry.flags |= EntryFlags.THUMBNAIL_ERROR
        return False

    # After processing all timestamps, check if ALL extracted thumbnails were too dark
    # Only consider this if there were thumbnails expected and extracted.
    if timestamps_to_extract and successful_extractions > 0:
        num_too_dark = 0
        num_valid_for_dark_check = 0
        for ts in timestamps_to_extract:
            tb_data = file_entry.gray_bytes_data.get(ts)
            if tb_data: # Only check thumbnails that were successfully extracted
                num_valid_for_dark_check += 1
                if are_gray_bytes_too_dark(tb_data):
                    num_too_dark += 1

        if num_valid_for_dark_check > 0 and num_too_dark == num_valid_for_dark_check:
            file_entry.flags |= EntryFlags.TOO_DARK

    if not successful_extractions and timestamps_to_extract:
        # If all attempts failed and thumbnails were expected
        # This implies THUMBNAIL_ERROR would already be set by individual failures
        return False

    # If at least one thumbnail succeeded or no thumbnails were expected, return True.
    # The THUMBNAIL_ERROR flag will indicate partial failure.
    # The TOO_DARK flag indicates if all valid extracted thumbnails were dark.
    return True


# TODO: Implement extract_thumbnail_image (for GUI previews, returns JPEG bytes)
# TODO: Implement extract_gray_bytes_for_segment (for segment comparison)
# TODO: Implement extract_thumbnails_for_segment (for segment comparison, color images)

# Need to import are_gray_bytes_too_dark
from .image_utils import are_gray_bytes_too_dark

logger = logging.getLogger(__name__)


DEFAULT_PREVIEW_THUMB_WIDTH = 100 # Default width for preview thumbnails if not specified
DEFAULT_PREVIEW_THUMB_HEIGHT = -1 # Tells ffmpeg to maintain aspect ratio based on width
DEFAULT_PREVIEW_THUMB_FORMAT = "mjpeg" # mjpeg is jpg. "png" could also be an option.


def extract_thumbnail_image(
    file_path: str,
    position_seconds: float, # If < 0 for image, ignored. If < 0 for video, a default like 1s or 10% could be used.
    settings: CoreSettings, # Needed for hwaccel, custom_args, logging
    output_size_wh: Optional[Tuple[Optional[int], Optional[int]]] = None # (width, height), None for original size or default scaling
) -> Optional[bytes]:
    """
    Extracts a color preview thumbnail from a video or image file.

    Args:
        file_path: Absolute path to the media file.
        position_seconds: Time in seconds for video frame. For images, or if <0 for video, may use default.
        settings: CoreSettings instance.
        output_size_wh: Optional tuple (width, height). Use None for ffmpeg default/original.
                        -1 for a dimension means auto-scale keeping aspect ratio.

    Returns:
        Image bytes (e.g., JPEG or PNG data) or None on failure.
    """
    if not os.path.exists(file_path):
        logger.warning(f"extract_thumbnail_image: File not found: {file_path}")
        return None

    is_image_file = file_path.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")) # Basic check

    actual_position_sec = position_seconds
    if is_image_file:
        actual_position_sec = 0 # For images, seeking isn't really a thing for a single frame
    elif actual_position_sec < 0: # Video with no specific position given
        # Default to 10% of duration, or 1s if duration is short or unknown
        # This requires getting media_info first if not already available.
        # For simplicity now, let's default to a small fixed time like 1s.
        # A more robust approach would be for the caller (VM) to determine the position.
        logger.debug(f"No specific position for video {file_path}, defaulting to 1s for preview (can be improved).")
        actual_position_sec = 1.0


    ffmpeg_input_args = {}
    if not is_image_file and actual_position_sec >= 0:
        ffmpeg_input_args['ss'] = actual_position_sec

    if settings.hardware_acceleration_mode not in [HardwareAcceleration.NONE, HardwareAcceleration.AUTO]:
        if not is_image_file: # HWAccel typically for video decoding
            ffmpeg_input_args['hwaccel'] = str(settings.hardware_acceleration_mode.value)
            # Note: Complex hwaccel pipelines might need hwaccel_output_format and vf filters
            # to bring pixel data to a CPU-usable format before encoding to JPEG/PNG.
            # This simplified version assumes ffmpeg can handle it or the output format is compatible.

    output_format = DEFAULT_PREVIEW_THUMB_FORMAT
    ffmpeg_output_args = {
        'format': output_format, # 'mjpeg' for jpg, or 'image2' with -c:v png for png
        'vframes': 1,
    }
    if output_format == "png": # If we want PNG
        ffmpeg_output_args['c:v'] = 'png'


    width, height = None, None
    if output_size_wh:
        width, height = output_size_wh

    if width is None and height is None: # If no size specified, use a default sensible preview size
        width = DEFAULT_PREVIEW_THUMB_WIDTH
        height = DEFAULT_PREVIEW_THUMB_HEIGHT # -1 for aspect ratio

    scale_filter = ""
    if width is not None or height is not None:
        w_str = str(width) if width is not None else "-1"
        h_str = str(height) if height is not None else "-1"
        if w_str == "0" or h_str == "0": # Avoid scale=0:0 if original size was requested via (0,0)
            pass # No scale filter, get original size
        else:
            scale_filter = f"scale={w_str}:{h_str}"
            # For images, -vf might not always work as expected if input is already image.
            # FFmpeg often auto-scales images with -s if outputting to image format.
            # If outputting to rawvideo, -s is needed. For image output, -s or -vf scale.
            # Using -vf scale for consistency.
            if is_image_file and output_format != 'rawvideo': # if outputting to an image format
                 # For direct image to image conversion, -s WxH is sometimes more direct.
                 # However, vf scale should also work.
                 # ffmpeg_output_args['s'] = f"{w_str}x{h_str}" # Alternative
                 pass # Rely on vf for now

    if scale_filter:
        ffmpeg_output_args['vf'] = scale_filter


    global_args_list = ['-hide_banner']
    if settings.extended_ffmpeg_logging:
        global_args_list.extend(['-loglevel', 'error'])
    else: # Add a default quiet for thumbnail generation if not extended
        global_args_list.extend(['-loglevel', 'quiet']) # more than error, less than info

    if settings.custom_ffmpeg_arguments:
        global_args_list.extend(settings.custom_ffmpeg_arguments.split())

    try:
        logger.debug(f"Extracting preview thumbnail for {file_path} at {actual_position_sec}s, target size {width}x{height}")
        process_chain = ffmpeg.input(file_path, **ffmpeg_input_args)
        process_chain = process_chain.output('pipe:', **ffmpeg_output_args)
        process_chain = process_chain.global_args(*global_args_list)

        out_bytes, err_bytes = process_chain.run(capture_stdout=True, capture_stderr=True) # Use run() not run_async()

        if err_bytes and settings.extended_ffmpeg_logging:
            # FFmpeg often outputs info to stderr even on success. Only log if it seems like an error.
            # A more robust check would be to see if "Error" or "failed" is in err_bytes.
            err_str = err_bytes.decode('utf-8', errors='ignore')
            if "error" in err_str.lower() or "failed" in err_str.lower() or "invalid" in err_str.lower():
                 logger.warning(f"FFmpeg stderr for preview {file_path}: {err_str}")

        if not out_bytes:
            logger.warning(f"No data returned from FFmpeg for preview thumbnail: {file_path}. Stderr: {err_bytes.decode('utf-8', errors='ignore') if err_bytes else 'N/A'}")
            return None
        return out_bytes

    except ffmpeg.Error as e:
        err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "Unknown ffmpeg error"
        if settings.extended_ffmpeg_logging:
            logger.error(f"FFmpeg error extracting preview for {file_path}: {err_msg}", exc_info=True)
        else:
            logger.warning(f"FFmpeg error extracting preview for {file_path}. Enable extended logs for details. Error: {err_msg[:100]}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error extracting preview for {file_path}: {str(e)}", exc_info=True)
        return None

# ```

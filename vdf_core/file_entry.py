"""
FileEntry and related data classes for VideoDuplicateFinder Python
Based on VDF.Core/FileEntry.cs and VDF.Core/MediaInfo.cs
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Flag, auto
from typing import Dict, Optional, List, Any # Any for raw_ffprobe_output initially
import os
import platform

# Assuming utils.py is in the same directory or package
from .utils import get_image_extensions, get_path_extension

class EntryFlags(Flag):
    NONE = 0
    IS_IMAGE = auto()
    MANUALLY_EXCLUDED = auto()
    METADATA_ERROR = auto() # Error fetching metadata from ffprobe
    THUMBNAIL_ERROR = auto() # Error generating one or more thumbnails
    TOO_DARK = auto() # All thumbnails for this entry are considered too dark
    # BROKEN_FILE = auto() # General flag if ffprobe can't read it at all

    def __str__(self):
        return self.name if self.name else "NONE"


@dataclass
class StreamInfo: # Simplified from C# MediaInfo.StreamInfo
    index: int
    codec_type: Optional[str] = None # 'video', 'audio'
    codec_name: Optional[str] = None
    # Video specific
    width: Optional[int] = None
    height: Optional[int] = None
    pix_fmt: Optional[str] = None
    frame_rate_avg: Optional[float] = None # avg_frame_rate as float
    # Audio specific
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    channel_layout: Optional[str] = None
    # Common
    bit_rate: Optional[int] = None # In bits per second
    duration: Optional[float] = None # Stream duration in seconds

@dataclass
class MediaInfo:
    format_name: Optional[str] = None # e.g., 'mov,mp4,m4a,3gp,3g2,mj2'
    format_long_name: Optional[str] = None
    duration_seconds: Optional[float] = None # Overall duration from format section
    size_bytes: Optional[int] = None # Overall size from format section
    bit_rate_bps: Optional[int] = None # Overall bitrate from format section

    streams: List[StreamInfo] = field(default_factory=list)

    # Store the raw ffprobe json output if detailed inspection is needed later
    # raw_ffprobe_output: Optional[Dict[str, Any]] = None # Can be large

    @property
    def video_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == 'video']

    @property
    def audio_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == 'audio']

    @property
    def primary_video_stream(self) -> Optional[StreamInfo]:
        # Often the first video stream, or one with pixel format if multiple exist
        # Heuristic: prefer stream with width/height if multiple video streams
        for s in self.video_streams:
            if s.width is not None and s.height is not None:
                return s
        if self.video_streams:
            return self.video_streams[0]
        return None

    @property
    def width(self) -> Optional[int]:
        pv_stream = self.primary_video_stream
        return pv_stream.width if pv_stream else None

    @property
    def height(self) -> Optional[int]:
        pv_stream = self.primary_video_stream
        return pv_stream.height if pv_stream else None

    @property
    def frame_rate(self) -> Optional[float]:
        pv_stream = self.primary_video_stream
        return pv_stream.frame_rate_avg if pv_stream else None


@dataclass
class FileEntry:
    path: str # Full absolute path to the file
    # folder: str # Automatically derived from path

    # Dictionary mapping a specific timestamp (in seconds, float) to the 16x16 grayscale bytes.
    # For images, the key is conventionally 0.0.
    # Optional[bytes] allows storing None if a specific thumbnail failed but others succeeded.
    gray_bytes_data: Dict[float, Optional[bytes]] = field(default_factory=dict)

    media_info: Optional[MediaInfo] = None
    flags: EntryFlags = EntryFlags.NONE

    # File system metadata
    date_created_utc: Optional[datetime] = None
    date_modified_utc: Optional[datetime] = None
    file_size_bytes: int = 0

    # Runtime flag, not typically persisted directly in this form.
    # Used by ScanEngine to mark entries that shouldn't be processed further in the current scan.
    is_invalid_for_scan: bool = False

    # Stores a hash or representation of the thumbnail_positions setting
    # used when gray_bytes_data was last generated for this entry.
    # Used for cache invalidation if thumbnail settings change.
    thumb_config_signature: Optional[str] = None

    def __post_init__(self):
        # Ensure path is absolute
        self.path = os.path.abspath(self.path)
        # Determine if IS_IMAGE based on extension
        if get_path_extension(self.path) in get_image_extensions():
            self.flags |= EntryFlags.IS_IMAGE

        # Populate file system metadata if not already provided (e.g., during initial creation)
        if self.date_modified_utc is None or self.file_size_bytes == 0:
            try:
                stat = os.stat(self.path)
                if self.date_created_utc is None:
                    try: # Creation time might not be available on all OS for os.stat
                        self.date_created_utc = datetime.utcfromtimestamp(stat.st_ctime)
                    except AttributeError: # Fallback for systems without st_ctime
                        self.date_created_utc = datetime.utcfromtimestamp(stat.st_mtime)
                if self.date_modified_utc is None:
                    self.date_modified_utc = datetime.utcfromtimestamp(stat.st_mtime)
                if self.file_size_bytes == 0:
                    self.file_size_bytes = stat.st_size
            except FileNotFoundError:
                self.flags |= EntryFlags.METADATA_ERROR # Or a more specific "FILE_NOT_FOUND"
                self.is_invalid_for_scan = True
            except Exception: # Catch other potential os.stat errors
                self.flags |= EntryFlags.METADATA_ERROR
                self.is_invalid_for_scan = True


    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    def __hash__(self):
        # Consistent hashing based on path (case-insensitive for Windows for dict keys/sets)
        if platform.system() == "Windows":
            return hash(self.path.lower())
        return hash(self.path)

    def __eq__(self, other):
        if not isinstance(other, FileEntry):
            return NotImplemented
        if platform.system() == "Windows":
            return self.path.lower() == other.path.lower()
        return self.path == other.path

    def to_dict(self) -> dict:
        """Converts FileEntry to a dictionary for serialization."""
        import base64
        gray_bytes_serializable = {
            str(ts): base64.b64encode(data).decode('ascii') if data else None
            for ts, data in self.gray_bytes_data.items()
        }

        return {
            "path": self.path,
            "gray_bytes_data": gray_bytes_serializable,
            "media_info": self.media_info.to_dict() if self.media_info else None,
            "flags": self.flags.value,
            "date_created_utc": self.date_created_utc.isoformat() if self.date_created_utc else None,
            "date_modified_utc": self.date_modified_utc.isoformat() if self.date_modified_utc else None,
            "file_size_bytes": self.file_size_bytes,
            "thumb_config_signature": self.thumb_config_signature,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FileEntry':
        """Creates FileEntry from a dictionary (e.g., loaded from JSON)."""
        import base64
        gray_bytes_deserialized = {
            float(ts_str): base64.b64decode(b64_data) if b64_data else None
            for ts_str, b64_data in data.get("gray_bytes_data", {}).items()
        }

        mi_data = data.get("media_info")
        media_info_obj = MediaInfo.from_dict(mi_data) if mi_data else None

        return cls(
            path=data["path"],
            gray_bytes_data=gray_bytes_deserialized,
            media_info=media_info_obj,
            flags=EntryFlags(data.get("flags", EntryFlags.NONE.value)),
            date_created_utc=datetime.fromisoformat(data["date_created_utc"]) if data.get("date_created_utc") else None,
            date_modified_utc=datetime.fromisoformat(data["date_modified_utc"]) if data.get("date_modified_utc") else None,
            file_size_bytes=data.get("file_size_bytes", 0),
            thumb_config_signature=data.get("thumb_config_signature")
        )

# Add to_dict and from_dict to StreamInfo and MediaInfo

def _dataclass_to_dict(obj):
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
    return {} # Should not happen for dataclasses

@classmethod
def streaminfo_from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional['StreamInfo']:
    if not data:
        return None
    # Manually map fields, handling potential missing keys or type conversions if necessary
    return cls(
        index=data.get('index', -1), # Provide default for critical fields if necessary
        codec_type=data.get('codec_type'),
        codec_name=data.get('codec_name'),
        width=data.get('width'),
        height=data.get('height'),
        pix_fmt=data.get('pix_fmt'),
        frame_rate_avg=data.get('frame_rate_avg'),
        sample_rate=data.get('sample_rate'),
        channels=data.get('channels'),
        channel_layout=data.get('channel_layout'),
        bit_rate=data.get('bit_rate'),
        duration=data.get('duration')
    )

StreamInfo.to_dict = _dataclass_to_dict
StreamInfo.from_dict = streaminfo_from_dict


@classmethod
def mediainfo_from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional['MediaInfo']:
    if not data:
        return None

    streams_data = data.get('streams', [])
    streams_list = [StreamInfo.from_dict(s_data) for s_data in streams_data if s_data]
    # Filter out None streams if any failed to deserialize, though from_dict should handle it
    streams_list = [s for s in streams_list if s is not None]


    return cls(
        format_name=data.get('format_name'),
        format_long_name=data.get('format_long_name'),
        duration_seconds=data.get('duration_seconds'),
        size_bytes=data.get('size_bytes'),
        bit_rate_bps=data.get('bit_rate_bps'),
        streams=streams_list
    )

MediaInfo.to_dict = lambda self: {
    "format_name": self.format_name,
    "format_long_name": self.format_long_name,
    "duration_seconds": self.duration_seconds,
    "size_bytes": self.size_bytes,
    "bit_rate_bps": self.bit_rate_bps,
    "streams": [s.to_dict() for s in self.streams if s] # Ensure s is not None
}
MediaInfo.from_dict = mediainfo_from_dict

# ```

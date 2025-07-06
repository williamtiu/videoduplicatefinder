"""
Configuration classes for VideoDuplicateFinder Python
Based on VDF.Core/Settings.cs and VDF.Core/ThumbnailPositionSetting.cs
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Set

class HardwareAcceleration(Enum):
    NONE = "none"
    AUTO = "auto" # ffmpeg-python might handle this
    VDPAU = "vdpau"
    DXVA2 = "dxva2"
    VAAPI = "vaapi"
    QSV = "qsv"
    CUDA = "cuda"
    VIDEOTOOLBOX = "videotoolbox"
    D3D11VA = "d3d11va"
    DRM = "drm"
    OPENCL = "opencl"
    MEDIACODEC = "mediacodec"
    VULKAN = "vulkan"

    def __str__(self):
        return self.value

class ThumbnailPositionType(Enum):
    PERCENTAGE = "percentage"
    OFFSET_FROM_START = "offset_from_start"
    OFFSET_FROM_END = "offset_from_end"

    def __str__(self):
        return self.value

@dataclass
class ThumbnailPositionSetting:
    type: ThumbnailPositionType = ThumbnailPositionType.PERCENTAGE
    value: float = 50.0  # Percentage (e.g., 50.0 for 50%) or Offset in seconds.

    def __str__(self):
        if self.type == ThumbnailPositionType.PERCENTAGE:
            return f"{self.value}%"
        elif self.type == ThumbnailPositionType.OFFSET_FROM_START:
            return f"{self.value}s from start"
        elif self.type == ThumbnailPositionType.OFFSET_FROM_END:
            return f"{self.value}s from end"
        return "Invalid Position"


@dataclass
class CoreSettings:
    # Paths
    include_list: Set[str] = field(default_factory=set)
    blacklist: Set[str] = field(default_factory=set) # Folders or specific file paths
    custom_database_folder: str = "" # If empty, use a default (e.g., in user app data or alongside script)

    # Filtering & Behavior
    ignore_read_only_folders: bool = False # Not directly applicable in Python's os.walk, more about skip logic
    ignore_reparse_points: bool = True # os.walk(follow_symlinks=False) can handle symlinks
    exclude_hard_links: bool = False # Requires checking inode numbers if on POSIX
    include_subdirectories: bool = True
    include_images: bool = True # Process image files
    scan_against_entire_database: bool = False
    enable_time_limited_scan: bool = False
    time_limit_seconds: int = 3600 * 24 * 7 # Default to 1 week

    # Comparison Tuning
    similarity_threshold_percent: float = 96.0
    duration_difference_percent: float = 20.0 # Allowed % diff in video durations
    compare_horizontally_flipped: bool = False
    ignore_black_pixels: bool = False
    ignore_white_pixels: bool = False

    # Thumbnail Generation
    thumbnail_positions: List[ThumbnailPositionSetting] = field(
        default_factory=lambda: [ThumbnailPositionSetting(ThumbnailPositionType.PERCENTAGE, 50.0)]
    )

    # Performance & Technical
    max_degree_of_parallelism: int = 1 # For CPU-bound tasks after I/O
    # use_native_ffmpeg_binding: bool = False # Not applicable, ffmpeg-python is the binding
    hardware_acceleration_mode: HardwareAcceleration = HardwareAcceleration.NONE
    custom_ffmpeg_arguments: str = "" # e.g. "-threads 4"
    extended_ffmpeg_logging: bool = False # More verbose ffmpeg output
    always_retry_failed_sampling: bool = False # Retry getting thumbnails even if previously failed
    include_non_existing_files: bool = True # Keep DB entries if file is missing

    # File Path/Size Filters (from C# version)
    filter_by_file_path_contains: bool = False
    file_path_contains_texts: List[str] = field(default_factory=list) # Simple text match, or glob patterns
    filter_by_file_path_not_contains: bool = False
    file_path_not_contains_texts: List[str] = field(default_factory=list)
    filter_by_file_size: bool = False
    maximum_file_size_mb: int = 10240 # 10 GB
    minimum_file_size_mb: int = 1 # 1 MB

    # Path for the settings file itself, not part of the core settings data but useful for management
    _settings_file_path: str = ""


    @property
    def difference_limit(self) -> float:
        """
        Calculates the maximum allowed difference ratio (0.0 to 1.0)
        based on the similarity_threshold_percent.
        E.g., if similarity_threshold_percent is 96.0, this returns 0.04.
        """
        return 1.0 - (self.similarity_threshold_percent / 100.0)

    def to_dict(self) -> dict:
        """Converts settings to a dictionary suitable for JSON serialization."""
        res = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"): continue # Skip private/meta fields

            if isinstance(value, Set):
                res[key] = sorted(list(value)) # Sort sets for consistent output
            elif isinstance(value, Enum):
                res[key] = value.value
            elif isinstance(value, list) and value and isinstance(value[0], ThumbnailPositionSetting):
                res[key] = [{"type": item.type.value, "value": item.value} for item in value]
            else:
                res[key] = value
        return res

    @classmethod
    def from_dict(cls, data: dict) -> 'CoreSettings':
        """Creates CoreSettings from a dictionary (e.g., loaded from JSON)."""
        settings = cls()
        for key, value in data.items():
            if hasattr(settings, key):
                attr = getattr(settings, key)
                if isinstance(attr, Set):
                    setattr(settings, key, set(value))
                elif isinstance(attr, Enum): # This requires getting the Enum member by value
                    enum_type = type(attr)
                    try:
                        setattr(settings, key, enum_type(value))
                    except ValueError:
                         # Handle cases where the value might not match an enum member, e.g. set a default
                        pass # Or log a warning
                elif key == "thumbnail_positions":
                     setattr(settings, key, [
                        ThumbnailPositionSetting(ThumbnailPositionType(item["type"]), item["value"])
                        for item in value
                    ])
                else:
                    setattr(settings, key, value)
        return settings

# Example of how to load/save settings (would typically be in a separate manager class or script)
# import json
# def save_settings(settings: CoreSettings, path: str):
#     with open(path, 'w') as f:
#         json.dump(settings.to_dict(), f, indent=4)

# def load_settings(path: str) -> CoreSettings:
#     try:
#         with open(path, 'r') as f:
#             data = json.load(f)
#         settings = CoreSettings.from_dict(data)
#         settings._settings_file_path = path
#         return settings
#     except FileNotFoundError:
#         return CoreSettings(_settings_file_path=path) # Return defaults if file not found
#     except Exception as e:
#         print(f"Error loading settings: {e}. Returning default settings.")
#         return CoreSettings(_settings_file_path=path)


def get_thumbnail_config_signature(thumbnail_positions: List[ThumbnailPositionSetting]) -> str:
    """
    Creates a stable string signature for a list of ThumbnailPositionSetting objects.
    Sorts by type and value to ensure signature is consistent regardless of original order.
    """
    if not thumbnail_positions:
        return "no_thumbnails"

    # Create a list of comparable tuples (type_name, value)
    comparable_list = sorted([(pos.type.value, pos.value) for pos in thumbnail_positions])

    # Join into a single string
    return ";".join([f"{t}:{v}" for t, v in comparable_list])

# ``` # This was the offending line

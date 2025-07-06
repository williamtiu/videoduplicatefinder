"""
Core Utilities for VideoDuplicateFinder Python
"""

# Based on VDF.Core/Utils/FileUtils.cs
_IMAGE_EXTENSIONS = {
    ".bmp", ".dib",
    ".jpeg", ".jpg", ".jpe",
    ".jp2",
    ".png",
    ".webp",
    ".pcx",
    ".tga",
    ".gif",
    ".tiff", ".tif",
    ".psd", ".psb", # Photoshop
    ".svg", # Scalable Vector Graphics
    ".heic", ".heif", # High Efficiency Image File Format
    ".avif" # AV1 Image File Format
}

# Based on VDF.Core/Utils/FileUtils.cs
# These are common video extensions. FFprobe will ultimately determine if a file is valid media.
_VIDEO_EXTENSIONS = {
    ".mkv", ".flv", ".f4v", ".vob", ".ogv", ".ogg", ".drc", ".gifv", ".mng",
    ".avi", ".mov", ".qt", ".wmv", ".yuv", ".rm", ".rmvb", ".viv", ".asf",
    ".amv", ".mp4", ".m4p", ".m4v", ".mpg", ".mp2", ".mpeg", ".mpe", ".mpv",
    ".m2v", ".svi", ".3gp", ".3g2", ".mxf", ".roq", ".nsv", ".flv", ".f4v",
    ".f4p", ".f4a", ".f4b", ".webm", ".mts", ".m2ts", ".ts"
}


def get_image_extensions() -> frozenset[str]:
    """Returns a frozenset of common image file extensions (lowercase, with leading dot)."""
    return frozenset(_IMAGE_EXTENSIONS)

def get_video_extensions() -> frozenset[str]:
    """Returns a frozenset of common video file extensions (lowercase, with leading dot)."""
    return frozenset(_VIDEO_EXTENSIONS)

def get_media_extensions() -> frozenset[str]:
    """Returns a frozenset of common image AND video file extensions."""
    return frozenset(_IMAGE_EXTENSIONS.union(_VIDEO_EXTENSIONS))

def is_image_file(file_path: str) -> bool:
    """Checks if the file path has a common image extension."""
    ext = "." + file_path.rsplit('.', 1)[-1].lower() if '.' in file_path else ""
    return ext in _IMAGE_EXTENSIONS

def is_video_file(file_path: str) -> bool:
    """Checks if the file path has a common video extension."""
    ext = "." + file_path.rsplit('.', 1)[-1].lower() if '.' in file_path else ""
    return ext in _VIDEO_EXTENSIONS

def get_path_extension(file_path: str) -> str:
    """Returns the file extension (lowercase, with leading dot), or empty string if no extension."""
    if '.' in file_path:
        return "." + file_path.rsplit('.', 1)[-1].lower()
    return ""

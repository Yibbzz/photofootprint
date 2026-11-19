"""Find a photo file from the path stored in the photo layer. Pure Python."""

import os


def layer_folder(layer_source: str) -> str:
    """Folder of a file-based layer source such as '/data/photos.gpkg|layername=photos'."""
    return os.path.dirname(layer_source.split("|")[0])


def resolve_photo_path(stored: str, layer_source: str) -> str:
    """Absolute path of a photo. Relative paths are relative to the layer's file."""
    if not stored:
        return ""
    if os.path.isabs(stored):
        return stored
    return os.path.normpath(os.path.join(layer_folder(layer_source), stored))

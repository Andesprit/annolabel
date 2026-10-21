"""Discover raster sources without relabeling generated AnnoLabel bundles."""

import os
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def discover_images(source: Path, output: Path) -> list[Path]:
    """Find unique sources recursively, excluding output and known generated bundles.

    :param source: Source directory.
    :param output: Folder-task destination to exclude.
    :returns: Sorted absolute image paths, deduplicated across symlink aliases.
    """
    images = set()

    def fail(error: OSError) -> None:
        raise error

    for root, directories, files in os.walk(source, onerror=fail):
        path = Path(root)
        if (
            path.resolve() == output
            or "packet.json" in files
            or "tasks.json" in files
            or (path / "annotations/instances_default.json").is_file()
        ):
            directories.clear()
            continue
        for name in files:
            image = path / name
            if image.suffix.lower() in IMAGE_SUFFIXES and image.is_file():
                images.add(image.resolve())
    return sorted(images)

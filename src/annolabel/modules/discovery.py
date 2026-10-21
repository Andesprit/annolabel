"""Discover raster sources without relabeling generated AnnoLabel bundles."""

import os
from collections.abc import Iterator
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
SIDECAR_SUFFIX = ".labels.json"
# Files that mark a directory as AnnoLabel output: task bundles, folder tasks, audits and previews.
GENERATED_MARKERS = {"packet.json", "tasks.json", "audit.json", "preview.json"}


def _walk(source: Path, output: Path) -> Iterator[tuple[Path, list[str]]]:
    def fail(error: OSError) -> None:
        raise error

    for root, directories, files in os.walk(source, onerror=fail):
        path = Path(root)
        if (
            path.resolve() == output
            or GENERATED_MARKERS.intersection(files)
            or (path / "annotations/instances_default.json").is_file()
        ):
            directories.clear()
            continue
        yield path, files


def discover_images(source: Path, output: Path) -> list[Path]:
    """Find unique sources recursively, excluding output and known generated bundles.

    :param source: Source directory.
    :param output: Folder-task destination to exclude.
    :returns: Sorted absolute image paths, deduplicated across symlink aliases.
    """
    images = set()
    for path, files in _walk(source, output):
        for name in files:
            image = path / name
            if image.suffix.lower() in IMAGE_SUFFIXES and image.is_file():
                images.add(image.resolve())
    return sorted(images)


def discover_sidecars(source: Path, output: Path) -> list[Path]:
    """Find annotation sidecars with the same pruning as source discovery.

    :param source: Source directory.
    :param output: Destination to exclude.
    :returns: Sorted sidecar paths as found (not resolved).
    """
    return sorted(
        path / name
        for path, files in _walk(source, output)
        for name in files
        if name.endswith(SIDECAR_SUFFIX) and len(name) > len(SIDECAR_SUFFIX)
    )

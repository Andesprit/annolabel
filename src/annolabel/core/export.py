"""Resolve source files and invoke the default dataset exporter."""

import json
from pathlib import Path

from annolabel.core.annolabel import AnnoLabel
from annolabel.modules.coco import export_coco
from annolabel.services.images.base import ImageServiceBase
from annolabel.services.images.local import LocalImageService


def export_dataset(
    source: str,
    output: str,
    categories_file: str | None = None,
    *,
    image_service: ImageServiceBase | None = None,
) -> dict:
    """Export one image or recursively discovered annotation sidecars.

    :param source: Image or directory containing sidecars.
    :param output: New dataset directory.
    :param categories_file: Optional ordered category vocabulary.
    :param image_service: Image reader; defaults to the local Pillow service.
    :returns: COCO export receipt and dataset paths.
    """
    service = image_service if image_service is not None else LocalImageService()
    path = Path(source).expanduser().resolve(strict=True)
    if path.is_dir():
        suffix = ".labels.json"
        sources = [p.with_name(p.name[: -len(suffix)]) for p in sorted(path.rglob("*" + suffix))]
        if not sources:
            raise ValueError("no annotation sidecars found in input directory")
    else:
        sources = [path]
    items = []
    seen = set()
    for image_path in sources:
        resolved = image_path.resolve(strict=True)
        if resolved in seen:
            continue
        seen.add(resolved)
        kit = AnnoLabel(str(resolved), image_service=service)
        items.append((kit.path, kit.document))
    categories = None
    if categories_file:
        categories = json.loads(Path(categories_file).expanduser().read_text())
        if not isinstance(categories, list):
            raise ValueError("categories file must contain a JSON array of label names")
    return export_coco(items, output, categories, image_service=service)

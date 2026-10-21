"""Resolve source files and invoke the default dataset exporter."""

from pathlib import Path
from shlex import quote

from annolabel.core.annolabel import AnnoLabel
from annolabel.core.inputs import read_categories
from annolabel.modules.coco import export_coco
from annolabel.modules.validation import InputError
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
            raise InputError(
                "NO_ANNOTATIONS",
                f"No annotation sidecars found in input directory {str(path)!r}.",
                f"Run annolabel task IMAGE --output NEW_TASK and annolabel submit TASK_JSON --file ANNOTATIONS_JSON "
                f"for the images in {str(path)!r}, then retry annolabel export {quote(str(path))} --output NEW_DATASET.",
            )
    else:
        sources = [path]
    items = []
    warnings = []
    seen = set()
    for image_path in sources:
        resolved = image_path.resolve(strict=True)
        if resolved in seen:
            continue
        seen.add(resolved)
        kit = AnnoLabel(str(resolved), image_service=service)
        if not kit.sidecar.exists():
            raise InputError(
                "NO_ANNOTATIONS",
                f"No annotation sidecar found for input image {str(resolved)!r}.",
                f"Run annolabel task {quote(str(resolved))} --output NEW_TASK, then "
                "annolabel submit TASK_JSON --file ANNOTATIONS_JSON before exporting. "
                "For an image with no objects, submit a snapshot with objects: [].",
            )
        if not any(a.kind != "label" for a in kit.document.annotations):
            warnings.append(
                f"Exported image {str(resolved)!r} with zero objects; whole-image classifications "
                "were preserved. Review this image before using the dataset for training."
            )
        items.append((kit.path, kit.document))
    categories = read_categories(categories_file) if categories_file else None
    result = export_coco(items, output, categories, image_service=service)
    if warnings:
        result["warnings"] = warnings
    return result

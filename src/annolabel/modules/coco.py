"""Standard COCO instances plus classification CSV and provenance."""

import csv
import json
import os
import shutil
import tempfile
from pathlib import Path

from annolabel.schemas.annotations import Document
from annolabel.services.images.base import ImageServiceBase


def export_coco(
    items: list[tuple[Path, Document]],
    output: str,
    categories: list[str] | None = None,
    *,
    image_service: ImageServiceBase,
) -> dict:
    """Write a portable dataset into a new output directory.

    :param items: Validated sources and annotation documents.
    :param output: New dataset directory.
    :param categories: Optional ordered vocabulary shared across splits.
    :param image_service: Source reader supplied by the core facade.
    :returns: Dataset paths and annotation counts.
    """
    destination = Path(output).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("export output already exists; choose a new directory")
    labels = sorted({a.label for _, doc in items for a in doc.annotations if a.kind != "label"})
    if categories is None:
        categories = labels
    if (
        not isinstance(categories, list)
        or any(not isinstance(name, str) or not name.strip() for name in categories)
        or len(categories) != len(set(categories))
    ):
        raise ValueError("categories must be a JSON array of unique nonempty label names")
    missing = set(labels) - set(categories)
    if missing:
        raise ValueError(f"category vocabulary is missing labels: {sorted(missing)}")
    category_ids = {name: index for index, name in enumerate(categories, 1)}
    coco = {
        "images": [],
        "annotations": [],
        "categories": [
            {"id": index, "name": name, "supercategory": ""} for name, index in category_ids.items()
        ],
    }
    provenance = {"images": [], "objects": []}
    classifications = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".annolabel-export-", dir=destination.parent))
    try:
        image_dir = temporary / "images/default"
        image_dir.mkdir(parents=True)
        (temporary / "annotations").mkdir()
        for image_id, (source, doc) in enumerate(items, 1):
            image, info = image_service.load(source)
            if info != doc.image:
                raise ValueError(f"source image changed during export: {source}")
            filename = f"{image_id:06d}.png"
            image.save(image_dir / filename)
            relative_image = f"images/default/{filename}"
            coco["images"].append(
                {"id": image_id, "file_name": filename, "width": info.width, "height": info.height}
            )
            provenance["images"].append(
                {
                    "image_id": image_id,
                    "source": str(source),
                    "sha256": info.sha256,
                    "exported_file": relative_image,
                }
            )
            objects = {}
            for annotation in doc.annotations:
                if annotation.kind == "label":
                    classifications.append(
                        [image_id, relative_image, annotation.label, annotation.note or ""]
                    )
                else:
                    objects.setdefault(annotation.object_id, []).append(annotation)
            for object_id, members in objects.items():
                polygon = next((a for a in members if a.kind == "polygon"), None)
                box = next((a for a in members if a.kind == "box"), None)
                points = box.points if box else polygon.points
                xs, ys = zip(*points)
                x, y, width, height = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
                annotation_id = len(coco["annotations"]) + 1
                record = {
                    "id": annotation_id,
                    "image_id": image_id,
                    "category_id": category_ids[members[0].label],
                    "bbox": [x, y, width, height],
                    "area": width * height,
                    "iscrowd": 0,
                }
                if polygon:
                    p = polygon.points
                    record["segmentation"] = [[coordinate for point in p for coordinate in point]]
                    record["area"] = (
                        abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(p, p[1:] + p[:1]))) / 2
                    )
                coco["annotations"].append(record)
                provenance["objects"].append(
                    {
                        "annotation_id": annotation_id,
                        "image_id": image_id,
                        "object_id": object_id,
                        "source_annotations": [a.model_dump(mode="json") for a in members],
                    }
                )
        annotation_file = "annotations/instances_default.json"
        (temporary / annotation_file).write_text(
            json.dumps(coco, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        )
        (temporary / "provenance.json").write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False) + "\n"
        )
        (temporary / "categories.json").write_text(
            json.dumps(categories, indent=2, ensure_ascii=False) + "\n"
        )
        with (temporary / "classifications.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["image_id", "file_name", "label", "note"])
            writer.writerows(classifications)
        (temporary / "README.txt").write_text(
            "COCO instances: annotations/instances_default.json\n"
            "Image root: images/default/ (COCO file_name is relative to this root).\n"
            "Images are losslessly encoded PNGs in the annotation coordinate orientation.\n"
            "One record per object. Polygons include segmentation; box-only objects omit it.\n"
            "Area is continuous polygon area, or box area for box-only objects.\n"
            "Whole-image labels are in classifications.csv; provenance retains IDs and notes.\n"
            "No train/validation split is inferred. Reuse categories.json for consistent class IDs.\n"
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError("export output appeared during export; choose a new directory")
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "format": "coco",
        "output": str(destination),
        "annotations": str(destination / annotation_file),
        "images": len(coco["images"]),
        "objects": len(coco["annotations"]),
        "categories": len(categories),
        "classifications": len(classifications),
    }

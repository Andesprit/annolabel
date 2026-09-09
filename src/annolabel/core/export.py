"""Resolve source files and invoke the default dataset exporter."""
import json
from pathlib import Path
from annolabel.core.annolabel import AnnoLabel
from annolabel.modules.coco import export_coco


def export_dataset(source: str, output: str, categories_file: str | None = None) -> dict:
    """Export one image or recursively discovered annotation sidecars."""
    path = Path(source).expanduser().resolve(strict=True)
    if path.is_dir():
        suffix = ".labels.json"
        sources = [p.with_name(p.name[:-len(suffix)]) for p in sorted(path.rglob("*" + suffix))]
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
        kit = AnnoLabel(str(resolved))
        items.append((kit.path, kit.document))
    categories = None
    if categories_file:
        categories = json.loads(Path(categories_file).expanduser().read_text())
        if not isinstance(categories, list):
            raise ValueError("categories file must contain a JSON array of label names")
    return export_coco(items, output, categories)

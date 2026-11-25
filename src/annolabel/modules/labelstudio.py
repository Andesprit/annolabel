"""Label Studio tasks with editable pre-annotations that point at the source images."""

import json
import os
import shutil
import tempfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

from annolabel.modules.images import COLORS
from annolabel.schemas.annotations import Annotation, Document

TAGS = {
    "box": ("RectangleLabels", "box", "rectanglelabels"),
    "polygon": ("PolygonLabels", "polygon", "polygonlabels"),
    "line": ("VectorLabels", "line", "vectorlabels"),
}


def _value(a: Annotation, width: int, height: int) -> dict:
    pct = [(round(x / width * 100, 4), round(y / height * 100, 4)) for x, y in a.points]
    if a.kind == "box":
        (x1, y1), (x2, y2) = pct
        return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1, "rotation": 0}
    if a.kind == "polygon":
        return {"points": [list(p) for p in pct], "closed": True}
    return {
        "vertices": [{"id": f"{a.id[:8]}-{i}", "x": x, "y": y} for i, (x, y) in enumerate(pct)],
        "closed": False,
    }


def export_labelstudio(items: list[tuple[Path, Document]], output: str, document_root: str) -> dict:
    """Write tasks.json and a matching config.xml into a new directory.

    :param items: Validated sources and annotation documents.
    :param output: New export directory.
    :param document_root: LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT; sources must lie inside it.
    :returns: Export receipt with tasks and config paths.
    """
    destination = Path(output).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("export output already exists; choose a new directory")
    root = Path(document_root).expanduser().resolve()
    tasks, labels, classes = [], {k: set() for k in TAGS}, set()
    for source, doc in items:
        if not source.is_relative_to(root):
            raise ValueError(f"{source} lies outside the document root {root}")
        results = []
        for a in doc.annotations:
            if a.kind == "label":
                classes.add(a.label)
                value = {"choices": [a.label]}
                tag, name = "choices", "class"
            else:
                _, name, tag = TAGS[a.kind]
                labels[a.kind].add(a.label)
                value = {**_value(a, doc.image.width, doc.image.height), tag: [a.label]}
            results.append(
                {
                    "id": a.id[:10],
                    "type": tag,
                    "from_name": name,
                    "to_name": "image",
                    "original_width": doc.image.width,
                    "original_height": doc.image.height,
                    "value": value,
                }
            )
        tasks.append(
            {
                "data": {"image": f"/data/local-files/?d={source.relative_to(root).as_posix()}"},
                "predictions": [{"model_version": "annolabel", "result": results}],
            }
        )
    # Same label-to-color mapping as the AnnoLabel views.
    ordered = sorted(set().union(*labels.values()))
    lines = ["<View>", '  <Image name="image" value="$image" zoomControl="true"/>']
    for kind, (tag, name, _) in TAGS.items():
        if labels[kind]:
            extra = ' closable="false" pointSize="small"' if kind == "line" else ""
            lines.append(f'  <{tag} name="{name}" toName="image"{extra}>')
            lines += [
                f"    <Label value={quoteattr(v)} "
                f'background="{COLORS[ordered.index(v) % len(COLORS)]}"/>'
                for v in sorted(labels[kind])
            ]
            lines.append(f"  </{tag}>")
    if classes:
        lines.append('  <Choices name="class" toName="image" choice="multiple">')
        lines += [f"    <Choice value={quoteattr(v)}/>" for v in sorted(classes)]
        lines.append("  </Choices>")
    lines.append("</View>")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".annolabel-export-", dir=destination.parent))
    try:
        (temporary / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n")
        (temporary / "config.xml").write_text("\n".join(lines) + "\n")
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "format": "labelstudio",
        "output": str(destination),
        "tasks": str(destination / "tasks.json"),
        "config": str(destination / "config.xml"),
        "document_root": str(root),
        "images": len(tasks),
    }

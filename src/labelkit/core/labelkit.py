"""Local annotation operations exposed to the CLI."""
import os
import tempfile
from pathlib import Path
from uuid import uuid4
from labelkit.modules.images import load_image, polygon_mask, render
from labelkit.schemas.annotations import Annotation, Document, Point


class LabelKit:
    """Open an image and its optional sidecar without modifying either."""
    def __init__(self, image_path: str) -> None:
        self.path = Path(image_path).expanduser().resolve(strict=True)
        self.sidecar = self.path.with_name(self.path.name + ".labels.json")
        self.image, info = load_image(self.path)
        if self.sidecar.exists():
            self.document = Document.model_validate_json(self.sidecar.read_text())
            if self.document.image != info:
                raise ValueError("source image differs from its annotation sidecar; restore the original image or move the old sidecar before starting again")
        else:
            self.document = Document(image=info)

    def info(self) -> dict:
        """Return source dimensions, annotation data, and sidecar location."""
        return {"image_path": str(self.path), "sidecar": str(self.sidecar),
                **self.document.model_dump(mode="json")}

    def _save(self, annotations: list[Annotation]) -> None:
        document = Document(image=self.document.image, annotations=annotations)
        # Same-directory replace prevents partially written annotation documents.
        fd, temporary = tempfile.mkstemp(prefix=".labelkit-", dir=self.sidecar.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(document.model_dump_json(indent=2) + "\n")
            os.replace(temporary, self.sidecar)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.document = document

    def annotate(self, kind: str, label: str, points: list[Point],
                 annotation_id: str | None = None, note: str | None = None,
                 object_id: str | None = None) -> dict:
        """Add an annotation, or replace an existing ID when supplied."""
        existing = self.document.annotations
        if annotation_id is not None and not any(a.id == annotation_id for a in existing):
            raise ValueError(f"annotation ID not found: {annotation_id}")
        previous = next((a for a in existing if a.id == annotation_id), None)
        if object_id is not None and not any(a.object_id == object_id for a in existing):
            raise ValueError(f"object ID not found: {object_id}")
        if previous and object_id is None and kind != "label":
            object_id = previous.object_id
        annotation = Annotation(id=annotation_id or uuid4().hex[:8], kind=kind,
                                label=label, points=points, note=note, object_id=object_id)
        if annotation_id:
            updated = [annotation if a.id == annotation_id else
                       a.model_copy(update={"label": label}) if
                       annotation.object_id is not None and a.object_id == annotation.object_id else a
                       for a in existing]
        else:
            updated = [*existing, annotation]
        self._save(updated)
        return {"sidecar": str(self.sidecar), "annotation": annotation.model_dump(mode="json")}

    def link(self, annotation_ids: list[str]) -> dict:
        """Join existing shapes into the first selected shape’s object."""
        if len(set(annotation_ids)) < 2:
            raise ValueError("link requires at least two different annotation IDs")
        by_id = {a.id: a for a in self.document.annotations}
        if any(i not in by_id for i in annotation_ids):
            raise ValueError("one or more annotation IDs were not found")
        selected = [by_id[i] for i in annotation_ids]
        if any(a.kind == "label" for a in selected):
            raise ValueError("whole-image labels cannot be linked to objects")
        groups = {a.object_id for a in selected}
        object_id = selected[0].object_id
        updated = [a.model_copy(update={"object_id": object_id}) if a.object_id in groups else a
                   for a in self.document.annotations]
        self._save(updated)
        return {"sidecar": str(self.sidecar), "object_id": object_id,
                "annotation_ids": [a.id for a in updated if a.object_id == object_id]}

    def remove(self, annotation_id: str) -> dict:
        """Remove exactly one existing annotation by ID."""
        updated = [a for a in self.document.annotations if a.id != annotation_id]
        if len(updated) == len(self.document.annotations):
            raise ValueError(f"annotation ID not found: {annotation_id}")
        self._save(updated)
        return {"sidecar": str(self.sidecar), "removed": annotation_id}

    def export_image(self, output: str, *, grid: int = 0,
                     annotation_id: str | None = None, force: bool = False) -> dict:
        """Write a preview or polygon mask as PNG, protecting source files."""
        destination = Path(output).expanduser().absolute()
        for protected in [self.path, self.sidecar]:
            if destination.resolve() == protected.resolve() or (
                destination.exists() and protected.exists() and destination.samefile(protected)
            ):
                raise ValueError("output must not overwrite the source image or annotation sidecar")
        if destination.suffix.lower() != ".png":
            raise ValueError("output must end with .png")
        result = (polygon_mask(self.document, annotation_id) if annotation_id
                  else render(self.image, self.document, grid))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb" if force else "xb") as handle:
            result.save(handle, format="PNG")
        return {"output": str(destination), "width": result.width, "height": result.height,
                "kind": "mask" if annotation_id else "preview"}

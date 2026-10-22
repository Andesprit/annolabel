"""Pure snapshot conversion; validated once before persistence."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from annolabel.modules.validation import InputError, validation_details
from annolabel.schemas.annotations import Annotation, Document
from annolabel.schemas.task import Submission
from annolabel.schemas.workflow import Batch, ClassificationInput, ObjectInput, Packet


class BatchValidationError(InputError):
    """Plain snapshot problems collected before any annotations are persisted."""

    def __init__(self, details: list[str]) -> None:
        super().__init__(
            "VALIDATION_ERROR",
            "; ".join(details),
            "Correct all listed fields and retry the same --file with the complete snapshot; "
            'use "classifications": [] when there are none. No annotation pass was saved.',
            details,
        )


def revision(document: Document) -> str:
    """Return a revision over the canonical validated document."""
    return hashlib.sha256(document.model_dump_json().encode()).hexdigest()


def snapshot(document: Document) -> dict:
    """Return an editable batch, preserving existing object identity."""
    objects = {}
    classifications = []
    for a in document.annotations:
        if a.kind == "label":
            classifications.append({"label": a.label, "note": a.note})
            continue
        obj = objects.setdefault(
            a.object_id, {"key": a.object_id, "label": a.label, "note": a.note}
        )
        obj[a.kind] = [*a.points[0], *a.points[1]] if a.kind == "box" else a.points
    return {
        "image_sha256": document.image.sha256,
        "base_revision": revision(document),
        "classifications": classifications,
        "objects": list(objects.values()),
    }


def check_packet(packet: Packet, document: Document) -> None:
    """Reject contexts belonging to another image or invalid transforms."""
    info = document.image
    if (packet.image_sha256, packet.width, packet.height) != (info.sha256, info.width, info.height):
        raise ValueError("packet does not match this source image")
    if len({v.id for v in packet.views}) != len(packet.views):
        raise ValueError("packet view IDs must be unique")
    for view in packet.views:
        x1, y1, x2, y2 = view.source_rect
        a, b, c, d = view.content_rect
        if not (
            0 <= x1 < x2 <= info.width and 0 <= y1 < y2 <= info.height and 0 <= a < c and 0 <= b < d
        ):
            raise ValueError(f"invalid transform for view {view.id}")


def _new_id(image_sha256: str, key: str, kind: str) -> str:
    # Preserve the historical ID namespace so saved checkpoints survive the rename.
    return uuid5(NAMESPACE_URL, f"labelkit:{image_sha256}:{kind}:{key}").hex


def _duplicates(items: list, collection: str, field: str) -> list[str]:
    seen = {}
    problems = []
    for index, item in enumerate(items):
        value = item.get(field) if isinstance(item, dict) else getattr(item, field, None)
        if not isinstance(value, str):
            continue
        if value in seen:
            problems.append(
                f"{collection}[{index}] ({value}): duplicate {field} {value!r}; "
                f"already used at {collection}[{seen[value]}]; use unique {field}s"
            )
        else:
            seen[value] = index
    return problems


def _object_annotations(
    obj: ObjectInput,
    document: Document,
    packet: Packet | None,
    ids: dict[tuple[str, str], str],
) -> tuple[list[Annotation], list[str]]:
    converted, problems = [], []
    if packet and packet.rules.categories is not None and obj.label not in packet.rules.categories:
        problems.append(
            f"label {obj.label!r} is not in the allowed categories: {packet.rules.categories}"
        )
    box, polygon = obj.box, obj.polygon
    if packet and packet.rules.geometry in {"both", "polygons"} and polygon is None:
        problems.append(f"task geometry={packet.rules.geometry!r} requires a polygon")
    if obj.view_id is not None:
        view = next((v for v in packet.views if v.id == obj.view_id), None) if packet else None
        if view is None:
            return [], [*problems, f"unknown view_id {obj.view_id!r}; supply its --packet"]
        x1, y1, x2, y2 = view.source_rect
        a, b, c, d = view.content_rect

        def source_point(p: tuple[float, float]) -> tuple[float, float]:
            x, y = p
            if not (a <= x <= c and b <= y <= d):
                raise ValueError(
                    "point lies outside view content (rulers/padding are not image pixels)"
                )
            return x1 + (x - a) * (x2 - x1) / (c - a), y1 + (y - b) * (y2 - y1) / (d - b)

        try:
            box = (*source_point(box[:2]), *source_point(box[2:])) if box is not None else None
            polygon = [source_point(p) for p in polygon] if polygon is not None else None
        except ValueError as error:
            return [], [*problems, str(error)]
    if box is None and polygon:
        xs, ys = zip(*polygon)
        box = (min(xs), min(ys), max(xs), max(ys))
    for kind, points in [
        ("box", [box[:2], box[2:]] if box is not None else None),
        ("polygon", polygon),
    ]:
        if points is not None:
            try:
                converted.append(
                    Annotation(
                        id=ids.get((obj.key, kind))
                        or _new_id(document.image.sha256, obj.key, kind),
                        object_id=obj.key,
                        kind=kind,
                        label=obj.label,
                        points=points,
                        note=obj.note,
                    )
                )
            except ValidationError as error:
                problems.extend(f"{kind}: {detail}" for detail in validation_details(error))
    try:
        Document(image=document.image, annotations=converted)
    except ValidationError as error:
        problems.extend(validation_details(error))
    return converted, problems


def parse_snapshot(
    payload: str,
    document: Document,
    packet: Packet | None = None,
    *,
    short: bool = False,
) -> Batch | Submission:
    """Collect schema errors alongside geometry errors in independently valid objects.

    Partial objects are used only for diagnostics, never returned or persisted.
    A valid input still follows the normal typed snapshot and revision checks.
    """
    schema = Submission if short else Batch
    try:
        return schema.model_validate_json(payload)
    except ValidationError as error:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            raise BatchValidationError(validation_details(error)) from None
        problems = validation_details(error, data)
    if isinstance(data, dict):
        objects = data.get("objects")
        if isinstance(objects, list):
            problems.extend(_duplicates(objects, "objects", "key"))
            for index, item in enumerate(objects):
                try:
                    obj = ObjectInput.model_validate(item)
                except ValidationError:
                    continue  # Its schema problems are already in the complete model's reply.
                _, details = _object_annotations(obj, document, packet, {})
                problems.extend(f"objects[{index}] ({obj.key}): {detail}" for detail in details)
        classifications = data.get("classifications")
        if isinstance(classifications, list):
            problems.extend(_duplicates(classifications, "classifications", "label"))
            for index, item in enumerate(classifications):
                try:
                    classification = ClassificationInput.model_validate(item)
                except ValidationError:
                    continue
                try:
                    Annotation(id="diagnostic", kind="label", **classification.model_dump())
                except ValidationError as error:
                    problems.extend(
                        f"classifications[{index}]: {detail}"
                        for detail in validation_details(error)
                    )
    raise BatchValidationError(problems)


def batch_document(batch: Batch, document: Document, packet: Packet | None = None) -> Document:
    """Validate and convert a complete batch without writing anything."""
    if batch.image_sha256 != document.image.sha256:
        raise ValueError("batch image_sha256 does not match this source")
    if batch.base_revision != revision(document):
        raise ValueError(
            "stale base_revision; run prepare again and reconcile with current annotations"
        )
    if packet:
        check_packet(packet, document)
    by_object_kind = {
        (a.object_id, a.kind): a.id for a in document.annotations if a.kind != "label"
    }
    by_class = {a.label: a.id for a in document.annotations if a.kind == "label"}
    problems = _duplicates(batch.objects, "objects", "key")
    problems.extend(_duplicates(batch.classifications, "classifications", "label"))
    annotations = []
    for index, c in enumerate(batch.classifications):
        try:
            annotations.append(
                Annotation(
                    id=by_class.get(c.label) or _new_id(document.image.sha256, c.label, "label"),
                    kind="label",
                    label=c.label,
                    note=c.note,
                )
            )
        except ValidationError as error:
            problems.extend(f"classifications[{index}]: {d}" for d in validation_details(error))
    for index, obj in enumerate(batch.objects):
        converted, details = _object_annotations(obj, document, packet, by_object_kind)
        annotations.extend(converted)
        problems.extend(f"objects[{index}] ({obj.key}): {detail}" for detail in details)
    if problems:
        raise BatchValidationError(problems)
    return Document(image=document.image, annotations=annotations)

"""Pure snapshot conversion; validated once before persistence."""

import hashlib
from uuid import NAMESPACE_URL, uuid5

from annolabel.schemas.annotations import Annotation, Document
from annolabel.schemas.workflow import Batch, Packet


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

    def new_id(key: str, kind: str) -> str:
        # Preserve the historical ID namespace so saved checkpoints survive the rename.
        return uuid5(NAMESPACE_URL, f"labelkit:{document.image.sha256}:{kind}:{key}").hex

    annotations = [
        Annotation(
            id=by_class.get(c.label) or new_id(c.label, "label"),
            kind="label",
            label=c.label,
            note=c.note,
        )
        for c in batch.classifications
    ]
    for obj in batch.objects:
        if (
            packet
            and packet.rules.categories is not None
            and obj.label not in packet.rules.categories
        ):
            raise ValueError(
                f"object {obj.key}: label {obj.label!r} is not in the allowed categories"
            )
        box, polygon = obj.box, obj.polygon
        if obj.view_id is not None:
            view = next((v for v in packet.views if v.id == obj.view_id), None) if packet else None
            if view is None:
                raise ValueError(f"unknown view_id {obj.view_id!r}; supply its --packet")
            x1, y1, x2, y2 = view.source_rect
            a, b, c, d = view.content_rect

            def source_point(p: tuple[float, float]) -> tuple[float, float]:
                x, y = p
                if not (a <= x <= c and b <= y <= d):
                    raise ValueError(
                        f"object {obj.key}: point lies outside view content (rulers/padding are not image pixels)"
                    )
                return x1 + (x - a) * (x2 - x1) / (c - a), y1 + (y - b) * (y2 - y1) / (d - b)

            box = (*source_point(box[:2]), *source_point(box[2:])) if box is not None else None
            polygon = [source_point(p) for p in polygon] if polygon is not None else None
        if box is None and polygon:
            xs, ys = zip(*polygon)
            box = (min(xs), min(ys), max(xs), max(ys))
        if packet and packet.rules.geometry in {"both", "polygons"} and polygon is None:
            raise ValueError(f"object {obj.key}: this task requires a polygon")
        for kind, points in [
            ("box", [box[:2], box[2:]] if box is not None else None),
            ("polygon", polygon),
        ]:
            if points is not None:
                annotations.append(
                    Annotation(
                        id=by_object_kind.get((obj.key, kind)) or new_id(obj.key, kind),
                        object_id=obj.key,
                        kind=kind,
                        label=obj.label,
                        points=points,
                        note=obj.note,
                    )
                )
    return Document(image=document.image, annotations=annotations)

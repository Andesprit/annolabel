"""Deterministic reference comparisons for the repository's small evaluation fixtures."""

from PIL import ImageChops

from annolabel.modules.batch import snapshot
from annolabel.modules.images import polygon_mask
from annolabel.schemas.annotations import Document


def _box(obj: dict) -> tuple:
    if obj.get("box") is not None:
        return tuple(obj["box"])
    xs, ys = zip(*obj["polygon"])
    return min(xs), min(ys), max(xs), max(ys)


def _iou(a: tuple, b: tuple) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union


def compare_documents(
    reference: Document, candidate: Document, aliases: dict[str, str] | None = None
) -> dict:
    """Compare object geometry and labels without changing either document.

    :param reference: Nonempty reference annotations for the same source image.
    :param candidate: Predictions, including any missed or extra objects.
    :param aliases: Explicit accepted label spellings, shared across every model.
    :returns: Greedy class-agnostic matches at box IoU >= 0.5 and separate label metrics.
    """
    if (reference.image.sha256, reference.image.width, reference.image.height) != (
        candidate.image.sha256,
        candidate.image.width,
        candidate.image.height,
    ):
        raise ValueError("reference and candidate must describe the same source image")
    expected = sorted(snapshot(reference)["objects"], key=lambda obj: obj["key"])
    predicted = sorted(snapshot(candidate)["objects"], key=lambda obj: obj["key"])
    if not expected:
        raise ValueError("evaluation requires at least one reference object")
    aliases = aliases or {}
    pairs = sorted(
        (
            (-_iou(_box(a), _box(b)), i, j)
            for i, a in enumerate(expected)
            for j, b in enumerate(predicted)
        ),
    )
    used_reference, used_candidate, matches = set(), set(), []
    for negative_iou, i, j in pairs:
        if -negative_iou < 0.5:
            break
        if i in used_reference or j in used_candidate:
            continue
        a, b = expected[i], predicted[j]
        mask_iou = None
        if a.get("polygon"):
            mask_iou = 0.0
            if b.get("polygon"):
                masks = []
                for doc, obj in ((reference, a), (candidate, b)):
                    annotation = next(
                        x
                        for x in doc.annotations
                        if x.object_id == obj["key"] and x.kind == "polygon"
                    )
                    masks.append(polygon_mask(doc, annotation.id))
                intersection = ImageChops.darker(*masks).histogram()[255]
                union = ImageChops.lighter(*masks).histogram()[255]
                mask_iou = intersection / union if union else 0.0
        used_reference.add(i)
        used_candidate.add(j)
        matches.append(
            {
                "reference_key": a["key"],
                "candidate_key": b["key"],
                "reference_label": a["label"],
                "candidate_label": b["label"],
                "box_iou": -negative_iou,
                "mask_iou": mask_iou,
                "label_correct": aliases.get(a["label"], a["label"])
                == aliases.get(b["label"], b["label"]),
            }
        )
    polygon_count = sum(bool(obj.get("polygon")) for obj in expected)
    return {
        "reference_objects": len(expected),
        "candidate_objects": len(predicted),
        "matched_objects": len(matches),
        "missed_objects": [obj["key"] for i, obj in enumerate(expected) if i not in used_reference],
        "extra_objects": [obj["key"] for i, obj in enumerate(predicted) if i not in used_candidate],
        "label_errors": sum(not match["label_correct"] for match in matches),
        "mean_box_iou_missing_zero": sum(match["box_iou"] for match in matches) / len(expected),
        "mean_mask_iou_missing_zero": (
            sum(match["mask_iou"] or 0.0 for match in matches) / polygon_count
            if polygon_count
            else None
        ),
        "matches": matches,
    }

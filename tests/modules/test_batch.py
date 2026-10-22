"""Pure snapshot conversion and stable object identities."""

import pytest

from annolabel.modules.batch import batch_document, revision, snapshot
from annolabel.schemas.annotations import Document
from annolabel.schemas.workflow import Batch


def test_snapshot_roundtrip_keeps_identity_and_derives_bounds(document: Document) -> None:
    batch = Batch(
        image_sha256=document.image.sha256,
        base_revision=revision(document),
        classifications=[],
        objects=[{"key": "leaf", "label": "leaf", "polygon": [[10, 10], [40, 10], [25, 50]]}],
    )
    annotated = batch_document(batch, document)
    editable = snapshot(annotated)
    assert editable["objects"][0]["box"] == [10, 10, 40, 50]
    assert batch_document(Batch.model_validate(editable), annotated) == annotated
    with pytest.raises(ValueError, match="stale"):
        batch_document(batch, annotated)


def test_schema_and_geometry_problems_are_reported_together(document: Document) -> None:
    import json

    from annolabel.modules.batch import BatchValidationError, parse_snapshot
    from annolabel.schemas.workflow import Packet, Rules

    packet = Packet(
        image_sha256=document.image.sha256,
        width=100,
        height=80,
        rules=Rules(categories=["leaf"], geometry="both"),
    )
    payload = {
        "objects": [
            {"label": "leaf", "box": [0, 0, 10, 10]},
            {"key": "wrong", "label": "car", "box": [9, 9, 1, 1]},
            {"key": "outside", "label": "leaf", "polygon": [[0, 0], [101, 0], [10, 10]]},
            {"key": "extra", "label": "leaf", "bbox": [0, 0, 10, 10]},
            {"key": "dup", "label": "leaf", "box": [0, 0, 10, 10]},
            {"key": "dup", "label": "leaf", "box": [20, 20, 30, 30]},
        ]
    }
    with pytest.raises(BatchValidationError) as caught:
        parse_snapshot(json.dumps(payload), document, packet, short=True)
    details = caught.value.details
    expected = [
        ("classifications", '"classifications": []'),
        ("objects[0]", "field key is required"),
        ("objects[1] (wrong)", "allowed categories"),
        ("objects[1] (wrong)", "x1 < x2"),
        ("objects[2] (outside)", "100x80"),
        ("objects[3] (extra)", "unknown field bbox; use box"),
        ("objects[4] (dup)", "geometry='both'"),
        ("objects[5] (dup)", "duplicate key 'dup'"),
    ]
    for location, message in expected:
        assert any(location in detail and message in detail for detail in details), details
    assert all(isinstance(detail, str) for detail in details)
    assert not any(
        noise in str(caught.value) for noise in ["Value error,", "input_value", "pydantic"]
    )


def test_duplicate_identity_and_classification_errors_are_collected(document: Document) -> None:
    from annolabel.modules.batch import BatchValidationError

    batch = Batch(
        image_sha256=document.image.sha256,
        base_revision=revision(document),
        classifications=[{"label": "scene"}, {"label": "scene"}, {"label": " "}],
        objects=[{"key": "dup", "label": "leaf", "box": [0, 0, 10, 10]}] * 2,
    )
    with pytest.raises(BatchValidationError) as caught:
        batch_document(batch, document)
    assert any("objects[1] (dup)" in d and "duplicate key" in d for d in caught.value.details)
    assert any("classifications[1]" in d and "duplicate label" in d for d in caught.value.details)
    assert any("classifications[2]" in d and "non-whitespace" in d for d in caught.value.details)

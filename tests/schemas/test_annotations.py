"""Annotation document validation and legacy sidecar compatibility."""

import pytest
from pydantic import ValidationError

from annolabel.schemas.annotations import Document


def test_legacy_objects_keep_distinct_identities(document: Document) -> None:
    data = document.model_dump()
    data.update(
        version=1,
        annotations=[
            {"id": "a", "kind": "box", "label": "car", "points": [[1, 1], [10, 10]]},
            {"id": "b", "kind": "box", "label": "car", "points": [[20, 20], [40, 40]]},
        ],
    )
    result = Document.model_validate(data)
    assert result.version == 2
    assert [a.object_id for a in result.annotations] == ["a", "b"]
    assert data["version"] == 1 and "object_id" not in data["annotations"][0]


def test_linked_box_must_contain_polygon(document: Document) -> None:
    data = document.model_dump()
    data["annotations"] = [
        {
            "id": "box",
            "object_id": "one",
            "kind": "box",
            "label": "leaf",
            "points": [[1, 1], [10, 10]],
        },
        {
            "id": "polygon",
            "object_id": "one",
            "kind": "polygon",
            "label": "leaf",
            "points": [[1, 1], [20, 1], [1, 20]],
        },
    ]
    with pytest.raises(ValidationError, match="must contain"):
        Document.model_validate(data)

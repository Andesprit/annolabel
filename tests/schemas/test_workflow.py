"""Full-snapshot contract checks without filesystem operations."""
import pytest
from pydantic import ValidationError
from annolabel.schemas.workflow import Batch, Rules


def test_duplicate_objects_reject_entire_snapshot() -> None:
    obj = {"key": "one", "label": "leaf", "box": [0, 0, 10, 10]}
    with pytest.raises(ValidationError, match="object keys must be unique"):
        Batch(image_sha256="source", base_revision="revision", classifications=[], objects=[obj, obj])


@pytest.mark.parametrize("categories", [["leaf", "leaf"], [" "]])
def test_invalid_category_vocabulary(categories: list[str]) -> None:
    with pytest.raises(ValidationError, match="unique nonempty"):
        Rules(categories=categories)

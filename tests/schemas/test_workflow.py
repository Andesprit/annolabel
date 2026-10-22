"""Full-snapshot contract checks without filesystem operations."""

import pytest
from pydantic import ValidationError

from annolabel.schemas.workflow import Rules


@pytest.mark.parametrize("categories", [["leaf", "leaf"], [" "]])
def test_invalid_category_vocabulary(categories: list[str]) -> None:
    with pytest.raises(ValidationError, match="unique nonempty"):
        Rules(categories=categories)

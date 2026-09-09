"""Short submissions keep task metadata out of the agent payload."""
import pytest
from pydantic import ValidationError
from annolabel.schemas.task import Submission, TaskHandle


def test_submission_rejects_injected_checkpoint_metadata() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        Submission.model_validate({"classifications": [], "objects": [], "base_revision": "override"})


@pytest.mark.parametrize("max_passes", [0, 3])
def test_task_handle_limits_annotation_passes(max_passes: int) -> None:
    with pytest.raises(ValidationError):
        TaskHandle(image="image", snapshot="snapshot", packet="packet", max_passes=max_passes)

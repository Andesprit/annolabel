"""Task handles keep persistence metadata out of agent submissions."""
from typing import Literal
from pydantic import Field
from labelkit.schemas.workflow import ClassificationInput, ObjectInput, StrictModel


class Submission(StrictModel):
    """Complete scene/object replacement supplied by the vision agent."""
    classifications: list[ClassificationInput]
    objects: list[ObjectInput]


class TaskHandle(StrictModel):
    """Immutable checkpoint for a task with at most two successful submissions."""
    version: Literal[1] = 1
    image: str
    snapshot: str
    packet: str
    pass_count: int = Field(default=0, ge=0, le=2)
    max_passes: int = Field(default=2, ge=1, le=2)

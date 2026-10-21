"""Task handles keep persistence metadata out of agent submissions."""

from typing import Literal

from pydantic import Field

from annolabel.schemas.workflow import ClassificationInput, ObjectInput, StrictModel


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


class FolderTaskEntry(StrictModel):
    """Source image and its ordinary initial task checkpoint."""

    image: str
    task: str
    view: str
    width: int
    height: int
    passes_left: int


class FolderTaskIndex(StrictModel):
    """Immutable index of independently submittable image tasks."""

    version: Literal[1] = 1
    kind: Literal["folder"] = "folder"
    source: str
    tasks: list[FolderTaskEntry] = Field(min_length=1)

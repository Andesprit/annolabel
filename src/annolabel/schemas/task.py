"""Task handles keep persistence metadata out of agent submissions."""

from typing import Literal

from pydantic import Field, model_validator

from annolabel.schemas.workflow import ClassificationInput, ObjectInput, Rules, StrictModel


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


class FolderStartFailure(StrictModel):
    """Source that could not start; task names the slot task-retry publishes into."""

    image: str
    task: str
    error: str
    code: str
    recovery: str


class FolderTaskIndex(StrictModel):
    """Immutable index of every discovered source and its image task slot.

    Rules are frozen at start so task-retry reuses them; legacy indexes omit them.
    """

    version: Literal[1] = 1
    kind: Literal["folder"] = "folder"
    source: str
    rules: Rules | None = None
    max_passes: int | None = Field(default=None, ge=1, le=2)
    tasks: list[FolderTaskEntry | FolderStartFailure] = Field(min_length=1)

    @model_validator(mode="after")
    def retry_rules(self) -> "FolderTaskIndex":
        if (self.rules is None or self.max_passes is None) and any(
            isinstance(entry, FolderStartFailure) for entry in self.tasks
        ):
            raise ValueError("startup failures require the frozen rules and max_passes")
        return self

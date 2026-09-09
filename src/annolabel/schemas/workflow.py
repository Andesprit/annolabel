"""Agent-facing annotation batches and coordinate view contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from annolabel.schemas.annotations import Point


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObjectInput(StrictModel):
    """Complete geometry for one object; key is stable across corrections."""

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1)
    box: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    polygon: list[Point] | None = None
    note: str | None = None
    view_id: str | None = None

    @model_validator(mode="after")
    def has_geometry(self) -> "ObjectInput":
        if self.box is None and self.polygon is None:
            raise ValueError("object needs a box or polygon")
        if not self.key.strip() or not self.label.strip():
            raise ValueError("object key and label cannot be blank")
        return self


class ClassificationInput(StrictModel):
    label: str = Field(min_length=1)
    note: str | None = None


class Batch(StrictModel):
    """A full replacement snapshot; omitted objects are deliberately removed."""

    image_sha256: str
    base_revision: str
    classifications: list[ClassificationInput]
    objects: list[ObjectInput]

    @model_validator(mode="after")
    def unique_keys(self) -> "Batch":
        keys = [obj.key for obj in self.objects]
        if len(keys) != len(set(keys)):
            raise ValueError("object keys must be unique")
        labels = [c.label for c in self.classifications]
        if len(labels) != len(set(labels)):
            raise ValueError("classification labels must be unique")
        return self


class Rules(StrictModel):
    """Researcher instructions plus machine-checkable object requirements."""

    instructions: str = (
        "Label every prominent object; describe visible appearance and note uncertainty."
    )
    categories: list[str] | None = None
    geometry: Literal["boxes", "polygons", "both"] = "both"
    boundary_policy: Literal["visible"] = "visible"

    @model_validator(mode="after")
    def category_names(self) -> "Rules":
        if self.categories is not None and (
            any(not c.strip() for c in self.categories)
            or len(set(self.categories)) != len(self.categories)
        ):
            raise ValueError("categories must be unique nonempty object labels")
        return self


class View(StrictModel):
    """Pixel transform for an explicitly identified panel in a PNG."""

    id: str
    path: str
    source_rect: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    content_rect: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


class Packet(StrictModel):
    """Source-bound viewing context; no inferred labels or geometry."""

    version: Literal[1] = 1
    image_sha256: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    rules: Rules = Field(default_factory=Rules)
    views: list[View] = Field(default_factory=list)


class Region(StrictModel):
    """An agent-selected region to enlarge before any annotations exist."""

    key: str = Field(min_length=1, max_length=100)
    box: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]

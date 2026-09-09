"""Validated, versioned annotation sidecars."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Point = tuple[FiniteFloat, FiniteFloat]


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    def on_segment(p: Point, q: Point, r: Point) -> bool:
        return (min(p[0], q[0]) <= r[0] <= max(p[0], q[0])
                and min(p[1], q[1]) <= r[1] <= max(p[1], q[1]))
    ab_c, ab_d, cd_a, cd_b = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    return ((ab_c * ab_d < 0 and cd_a * cd_b < 0)
            or (ab_c == 0 and on_segment(a, b, c))
            or (ab_d == 0 and on_segment(a, b, d))
            or (cd_a == 0 and on_segment(c, d, a))
            or (cd_b == 0 and on_segment(c, d, b)))


class Annotation(BaseModel):
    """One image label, bounding box, or simple polygon."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    object_id: str | None = Field(default=None, min_length=1)
    kind: Literal["label", "box", "polygon"]
    label: str = Field(min_length=1)
    points: list[Point] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def validate_geometry(self) -> "Annotation":
        """Reject empty labels and invalid geometry."""
        if not self.label.strip():
            raise ValueError("label must contain non-whitespace characters")
        if self.kind == "label" and self.object_id is not None:
            raise ValueError("whole-image labels cannot have an object ID")
        if self.kind != "label" and self.object_id is None:
            self.object_id = self.id
        p = self.points
        if self.kind == "label" and p:
            raise ValueError("whole-image labels cannot contain points")
        if self.kind == "box":
            if len(p) != 2 or p[0][0] >= p[1][0] or p[0][1] >= p[1][1]:
                raise ValueError("box requires x1 < x2 and y1 < y2")
        if self.kind == "polygon":
            if len(p) < 3 or len(set(p)) != len(p):
                raise ValueError("polygon needs at least three distinct points; do not repeat the first point")
            area = sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(p, p[1:] + p[:1]))
            if area == 0:
                raise ValueError("polygon must have nonzero area")
            edges = list(zip(p, p[1:] + p[:1]))
            for i, (a, b) in enumerate(edges):
                for j in range(i + 1, len(edges)):
                    if j == i + 1 or (i == 0 and j == len(edges) - 1):
                        continue
                    if _intersects(a, b, *edges[j]):
                        raise ValueError("polygon must not intersect itself")
        return self


class ImageInfo(BaseModel):
    """Identity and dimensions of the EXIF-oriented source image."""
    model_config = ConfigDict(extra="forbid")
    file: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    sha256: str


class Document(BaseModel):
    """On-disk JSON annotation document."""
    model_config = ConfigDict(extra="forbid")
    version: Literal[2] = 2
    coordinates: Literal["exif-oriented-pixels"] = "exif-oriented-pixels"
    image: ImageInfo
    annotations: list[Annotation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def upgrade_v1(cls, data: object) -> object:
        """Read old sidecars without guessing which shapes belong together."""
        if isinstance(data, dict) and data.get("version") == 1:
            return {**data, "version": 2}
        return data

    @model_validator(mode="after")
    def validate_bounds(self) -> "Document":
        """Check annotation IDs and image bounds."""
        ids = [a.id for a in self.annotations]
        if len(ids) != len(set(ids)):
            raise ValueError("annotation IDs must be unique")
        for annotation in self.annotations:
            for x, y in annotation.points:
                if not (0 <= x <= self.image.width and 0 <= y <= self.image.height):
                    raise ValueError(f"point ({x}, {y}) lies outside {self.image.width}x{self.image.height} image")
        objects: dict[str, list[Annotation]] = {}
        for annotation in self.annotations:
            if annotation.object_id is not None:
                objects.setdefault(annotation.object_id, []).append(annotation)
        for object_id, members in objects.items():
            if len({a.label for a in members}) != 1:
                raise ValueError(f"object {object_id} has conflicting labels")
            if len({a.kind for a in members}) != len(members):
                raise ValueError(f"object {object_id} can have only one box and one polygon")
            box = next((a for a in members if a.kind == "box"), None)
            polygon = next((a for a in members if a.kind == "polygon"), None)
            if box and polygon:
                (x1, y1), (x2, y2) = box.points
                if any(not (x1 <= x <= x2 and y1 <= y <= y2) for x, y in polygon.points):
                    raise ValueError(f"box for object {object_id} must contain its polygon")
        return self

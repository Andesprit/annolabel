"""Point-in-time dataset audit report; read-only observations, not review status."""

from typing import Literal

from annolabel.schemas.workflow import StrictModel


class AuditObject(StrictModel):
    """One saved object; a linked box and polygon count once."""

    tag: int
    object_id: str
    label: str
    geometry: list[Literal["box", "polygon"]]
    annotation_ids: list[str]
    notes: list[str]


class AuditClassification(StrictModel):
    """One saved whole-image (scene) class."""

    id: str
    label: str
    note: str | None


class AuditSource(StrictModel):
    """One eligible source image and what was observed about it."""

    id: int
    image: str
    path: str
    sidecar: str
    status: Literal["saved", "unannotated", "error"]
    page: str
    objects: list[AuditObject] = []
    classifications: list[AuditClassification] = []
    actions: dict[str, str] = {}
    code: str | None = None
    error: str | None = None
    details: list[str] | None = None
    recovery: str | None = None


class OrphanSidecar(StrictModel):
    """A sidecar that does not belong to any eligible source image."""

    sidecar: str
    source: str
    code: Literal["ORPHAN_SIDECAR"] = "ORPHAN_SIDECAR"
    error: str
    recovery: str


class AuditTotals(StrictModel):
    """Source counts by observed state."""

    sources: int
    saved: int
    saved_zero_objects: int
    unannotated: int
    errors: int
    orphan_sidecars: int
    objects: int
    classifications: int


class AuditReport(StrictModel):
    """Self-contained audit.json written beside the overview pages."""

    version: Literal[1] = 1
    observed_at: str
    source: str
    note: str
    totals: AuditTotals
    object_labels: dict[str, int]
    object_geometry: dict[str, int]
    scene_classes: dict[str, int]
    pages: list[str]
    sources: list[AuditSource]
    orphan_sidecars: list[OrphanSidecar]

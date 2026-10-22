"""Read-only dataset audit: inventory, label counts and overview pages for a source folder."""

import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from shlex import quote

from annolabel.core.inputs import read_categories, read_model
from annolabel.modules.batch import revision
from annolabel.modules.discovery import (
    IMAGE_SUFFIXES,
    SIDECAR_SUFFIX,
    discover_images,
    discover_sidecars,
)
from annolabel.modules.overview import OverviewPages
from annolabel.modules.validation import InputError
from annolabel.schemas.annotations import Document
from annolabel.schemas.audit import (
    AuditClassification,
    AuditObject,
    AuditReport,
    AuditSource,
    AuditTotals,
    NoteItem,
    NoteQueue,
    NoteRegion,
    OrphanSidecar,
    VocabularyMismatch,
)
from annolabel.services.images.base import ImageServiceBase
from annolabel.services.images.local import LocalImageService

NOTE = (
    "Point-in-time observation of the source folder. 'saved' means a valid annotation sidecar "
    "matched the image when audited; it does not mean reviewed, semantically correct or training "
    "ready. Labels are counted literally (case-sensitive, never merged). Overview pages are "
    "thumbnails for spotting problems; use the per-source review action for boundaries. A 'correct' "
    "action starts a NEW task with fresh passes from the saved labels; it does not resume an "
    "earlier task handle. Replace the NEW_..._DIR placeholders with directories that do not exist."
)

QUEUE_NOTE = (
    "Point-in-time reading list of saved objects and scene classes with explicit nonblank notes. "
    "A note is literal researcher text (it may be bookkeeping); it is not an uncertainty, "
    "confidence or review status, and items without notes are not reviewed or correct. Linked box "
    "and polygon geometry is one item; repeated identical note text is listed once. An object's "
    "review action enlarges its saved region: other annotations inside that crop are drawn too, so "
    "the target is the item's region key, not every outline shown. A classification's action "
    "reviews the whole image; open its overview.png. Actions load the CURRENT saved labels and do "
    "not check this item's image_sha256 or annotation_revision; after changes, audit again to a "
    "new --output. Replace NEW_REVIEW_DIR with a directory that does not exist."
)


def _noted(entry: AuditSource, document: Document, start: int, folder: Path) -> list[NoteItem]:
    """List noted objects and classes of one saved source, numbered from start."""
    by_id = {a.id: a for a in document.annotations}
    base = {
        "source_id": entry.id,
        "image": entry.image,
        "path": entry.path,
        "image_sha256": document.image.sha256,
        "annotation_revision": revision(document),
    }
    items = []
    for obj in entry.objects:
        members = [by_id[i] for i in obj.annotation_ids]
        notes = list(dict.fromkeys(a.note for a in members if a.note and a.note.strip()))
        if not notes:
            continue
        name = f"note-{start + len(items):03}"
        xs, ys = zip(*(p for a in members for p in a.points))
        file = folder / "notes" / f"{name}.json"
        region = NoteRegion(
            key=f"{name} {obj.label}"[:100],
            box=(min(xs), min(ys), max(xs), max(ys)),
            file=str(file),
        )
        items.append(
            NoteItem(
                **base,
                item=name,
                kind="object",
                label=obj.label,
                notes=notes,
                annotation_ids=obj.annotation_ids,
                object_id=obj.object_id,
                tag=obj.tag,
                geometry=obj.geometry,
                region=region,
                review=f"annolabel review {quote(entry.path)} --regions {quote(str(file))} "
                "--output NEW_REVIEW_DIR",
                open="review-001.png in NEW_REVIEW_DIR: the crop titled with this region key",
            )
        )
    for classification in entry.classifications:
        if not (classification.note and classification.note.strip()):
            continue
        items.append(
            NoteItem(
                **base,
                item=f"note-{start + len(items):03}",
                kind="classification",
                label=classification.label,
                notes=[classification.note],
                annotation_ids=[classification.id],
                review=f"annolabel review {quote(entry.path)} --output NEW_REVIEW_DIR",
                open="overview.png in NEW_REVIEW_DIR: the whole image this scene class describes",
            )
        )
    return items


def _inspect(
    service: ImageServiceBase, image: Path, number: int, rel: str, page: str
) -> tuple[AuditSource, object, Document | None]:
    sidecar = image.with_name(image.name + SIDECAR_SUFFIX)
    base = {"id": number, "image": rel, "path": str(image), "sidecar": str(sidecar), "page": page}
    review = f"annolabel review {quote(str(image))} --output NEW_REVIEW_DIR"
    task = f"annolabel task {quote(str(image))} --output NEW_TASK_DIR"
    try:
        pixels, info = service.load(image)
    except (OSError, ValueError) as error:
        kept = f" Its sidecar {str(sidecar)!r} was not read or changed." if sidecar.exists() else ""
        source = AuditSource(
            **base,
            status="error",
            code="IMAGE_UNREADABLE",
            error=f"Cannot read image: {getattr(error, 'strerror', None) or error}.",
            recovery=f"Restore or re-export {quote(str(image))} as a single-frame image, or move it out "
            f"of the folder; then audit again to a new --output.{kept}",
        )
        return source, None, None
    if not sidecar.exists():
        actions = {"label": task}
        return AuditSource(**base, status="unannotated", actions=actions), pixels, None
    try:
        document = read_model(str(sidecar), Document, "annotation sidecar", "")
    except InputError as error:
        source = AuditSource(
            **base,
            status="error",
            code="SIDECAR_INVALID",
            error=str(error),
            details=error.details,
            recovery=f"Restore {quote(str(sidecar))} from a backup or version control. To discard it, "
            f"move it out of the folder and relabel with: {task}",
        )
        return source, pixels, None
    if document.image != info:
        saved = document.image
        source = AuditSource(
            **base,
            status="error",
            code="SIDECAR_STALE",
            error=f"Image changed after labeling: sidecar records {saved.width}x{saved.height} "
            f"sha256 {saved.sha256[:12]}, image is {info.width}x{info.height} sha256 {info.sha256[:12]}.",
            recovery=f"Restore the original {quote(str(image))}. If the new image is intended, move "
            f"{quote(str(sidecar))} out of the folder and relabel with: {task}",
        )
        return source, pixels, None
    groups: dict[str, list] = {}
    for annotation in document.annotations:
        if annotation.object_id is not None:
            groups.setdefault(annotation.object_id, []).append(annotation)
    objects = [
        AuditObject(
            tag=tag,
            object_id=object_id,
            label=members[0].label,
            geometry=sorted(a.kind for a in members),
            annotation_ids=[a.id for a in members],
            notes=[a.note for a in members if a.note],
        )
        for tag, (object_id, members) in enumerate(groups.items(), start=1)
    ]
    classifications = [
        AuditClassification(id=a.id, label=a.label, note=a.note)
        for a in document.annotations
        if a.kind == "label"
    ]
    # The default rule requires polygons; boxes keeps saved box-only objects submittable.
    boxes = any("polygon" not in o.geometry for o in objects)
    actions = {
        "review": review,
        "correct": f"annolabel task {quote(str(image))} --geometry boxes --output NEW_TASK_DIR"
        if boxes
        else task,
    }
    source = AuditSource(
        **base,
        status="saved",
        objects=objects,
        classifications=classifications,
        actions=actions,
    )
    return source, pixels, document


def _orphan(sidecar: Path) -> OrphanSidecar:
    source = sidecar.with_name(sidecar.name[: -len(SIDECAR_SUFFIX)])
    if not source.exists():
        error = f"Source {str(source)!r} for sidecar {str(sidecar)!r} does not exist."
        recovery = (
            f"Restore {quote(str(source))} beside the sidecar, or move {quote(str(sidecar))} out of "
            "the folder. Folder export fails while it is present."
        )
    else:
        error = (
            f"Sidecar {str(sidecar)!r} belongs to {str(source)!r}, which is not a supported source "
            f"image ({', '.join(sorted(IMAGE_SUFFIXES))}) or lies in a generated AnnoLabel folder."
        )
        recovery = (
            f"Move {quote(str(sidecar))} out of the folder if it is not an annotation for a source "
            "image. Folder export reads every sidecar and may fail on it."
        )
    return OrphanSidecar(sidecar=str(sidecar), source=str(source), error=error, recovery=recovery)


def _mismatches(
    sources: list[AuditSource], categories: list[str], snapshot: Path
) -> list[VocabularyMismatch]:
    allowed = set(categories)
    return [
        VocabularyMismatch(
            source_id=s.id,
            image=s.image,
            path=s.path,
            label=o.label,
            object_id=o.object_id,
            annotation_ids=o.annotation_ids,
            geometry=o.geometry,
            review=s.actions["review"],
            # boxes is the least restrictive rule: it never adds a polygon requirement.
            correct=f"annolabel task {quote(s.path)} --categories {quote(str(snapshot))} "
            "--geometry boxes --output NEW_TASK_DIR",
        )
        for s in sources
        if s.status == "saved"
        for o in s.objects
        if o.label not in allowed
    ]


def audit_folder(
    source: str,
    output: str,
    *,
    categories: str | None = None,
    image_service: ImageServiceBase | None = None,
) -> dict:
    """Inspect every source image and sidecar below a folder without changing them.

    :param source: Source directory.
    :param output: New report directory (published atomically).
    :param categories: Optional JSON file of allowed object labels; None checks nothing.
    :param image_service: Image reader; defaults to the local Pillow service.
    :returns: Reply with status, report/page paths, totals and issues.
    """
    service = image_service if image_service is not None else LocalImageService()
    vocabulary = read_categories(categories) if categories is not None else None
    source_path = Path(source).expanduser().resolve(strict=True)
    if not source_path.is_dir():
        raise InputError(
            "NOT_A_FOLDER",
            f"audit inspects a folder; {str(source_path)!r} is a file.",
            f"For one image run annolabel review {quote(str(source_path))} --output NEW_REVIEW_DIR, "
            "or pass its folder to annolabel audit FOLDER --output NEW_AUDIT_DIR.",
        )
    destination = Path(output).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise InputError(
            "OUTPUT_EXISTS",
            f"Output directory already exists: {str(destination)!r}.",
            "Pass a new directory with --output; the existing directory was not replaced.",
        )
    images = discover_images(source_path, destination.resolve())
    eligible = set(images)
    orphans = [
        _orphan(s)
        for s in discover_sidecars(source_path, destination.resolve())
        if s.with_name(s.name[: -len(SIDECAR_SUFFIX)]).resolve() not in eligible
    ]
    if not images and not orphans:
        raise InputError(
            "NO_IMAGES",
            f"No source images found in input directory {str(source_path)!r}.",
            "Pass a folder containing source images (supported extensions: "
            + ", ".join(sorted(IMAGE_SUFFIXES))
            + "); generated AnnoLabel bundles are excluded.",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".annolabel-audit-", dir=destination.parent))
    try:
        notice = (
            f"{len(orphans)} sidecar(s) without a source image are listed only in audit.json "
            "orphan_sidecars; they need action."
            if orphans
            else "Full per-source details, actions and recovery steps: audit.json."
        )
        pages = OverviewPages(temporary, len(images), notice)
        sources = []
        noted: list[NoteItem] = []
        for number, image in enumerate(images, start=1):
            try:
                rel = image.relative_to(source_path).as_posix()
            except ValueError:  # reached through a symlinked directory
                rel = str(image)
            page = str(destination / OverviewPages.name(number))
            entry, pixels, document = _inspect(service, image, number, rel, page)
            pages.add(entry, pixels, document)
            sources.append(entry)
            if document is not None:
                noted += _noted(entry, document, len(noted) + 1, destination)
            del pixels, document
        saved = [s for s in sources if s.status == "saved"]
        objects = [o for s in saved for o in s.objects]
        observed = datetime.now(UTC).isoformat(timespec="seconds")
        (temporary / "notes").mkdir()
        for item in noted:
            if item.region is not None:
                region = [{"key": item.region.key, "box": list(item.region.box)}]
                (temporary / "notes" / f"{item.item}.json").write_text(json.dumps(region) + "\n")
        queue = NoteQueue(
            observed_at=observed,
            source=str(source_path),
            note=QUEUE_NOTE,
            count=len(noted),
            items=noted,
        )
        (temporary / "notes.json").write_text(queue.model_dump_json(indent=2) + "\n")
        snapshot = destination / "categories.json"
        mismatches = []
        if vocabulary is not None:
            (temporary / "categories.json").write_text(json.dumps(vocabulary) + "\n")
            mismatches = _mismatches(sources, vocabulary, snapshot)
        report = AuditReport(
            observed_at=observed,
            source=str(source_path),
            note=NOTE,
            totals=AuditTotals(
                sources=len(sources),
                saved=len(saved),
                saved_zero_objects=sum(not s.objects for s in saved),
                unannotated=sum(s.status == "unannotated" for s in sources),
                errors=sum(s.status == "error" for s in sources),
                orphan_sidecars=len(orphans),
                objects=len(objects),
                classifications=sum(len(s.classifications) for s in saved),
            ),
            object_labels=dict(sorted(Counter(o.label for o in objects).items())),
            object_geometry=dict(sorted(Counter("+".join(o.geometry) for o in objects).items())),
            scene_classes=dict(
                sorted(Counter(c.label for s in saved for c in s.classifications).items())
            ),
            pages=sorted({s.page for s in sources}),
            sources=sources,
            orphan_sidecars=orphans,
            noted_items=len(noted),
            notes=str(destination / "notes.json"),
            categories=vocabulary,
            category_file=str(snapshot) if vocabulary is not None else None,
            vocabulary_mismatches=mismatches,
        )
        (temporary / "audit.json").write_text(report.model_dump_json(indent=2) + "\n")
        os.rename(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    totals = report.totals
    issues = [
        {"id": s.id, "image": s.path, "code": s.code, "recovery": s.recovery}
        for s in sources
        if s.status == "error"
    ] + [{"sidecar": o.sidecar, "code": o.code, "recovery": o.recovery} for o in orphans]
    attention = bool(totals.unannotated or totals.errors or totals.orphan_sidecars or mismatches)
    steps = []
    if totals.unannotated:
        steps.append(
            f"{totals.unannotated} source(s) have no annotations (status unannotated in the report); "
            "label them with their 'label' action or leave them out deliberately."
        )
    if issues:
        steps.append(f"Resolve the {len(issues)} listed issue(s) using each recovery step.")
    if mismatches:
        steps.append(
            f"{len(mismatches)} saved object(s) have labels outside the chosen object vocabulary "
            "(case-sensitive; scene classes are not checked): audit.json vocabulary_mismatches "
            "lists each with its review and correct actions. A correct action starts a NEW task "
            "with fresh passes from the saved labels, using the audited category snapshot."
        )
    if noted:
        steps.append(
            f"{len(noted)} saved item(s) carry explicit notes: notes.json lists each with its "
            "literal text and a focused review action (a note is not an uncertainty or review status)."
        )
    steps.append(
        "Open every page, then use a source's review action for detail. After changes, audit again "
        "to a new --output; this report is not updated."
    )
    return {
        "status": "attention" if attention else "ready",
        "report": str(destination / "audit.json"),
        "pages": report.pages,
        "totals": totals.model_dump(),
        "object_labels": report.object_labels,
        "scene_classes": report.scene_classes,
        "issues": issues,
        "noted_items": len(noted),
        "notes": report.notes,
        **(
            {
                "categories": vocabulary,
                "category_file": report.category_file,
                "vocabulary_mismatches": len(mismatches),
            }
            if vocabulary is not None
            else {}
        ),
        "next": " ".join(steps),
        "note": "Saved is not reviewed or training ready; see the report note.",
    }

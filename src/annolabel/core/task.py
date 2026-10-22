"""Short annotation tasks with immutable checkpoints and compact receipts."""

import hashlib
import json
import shutil
from pathlib import Path
from shlex import quote

from pydantic import ValidationError

from annolabel.core.inputs import parse_json, read_model, read_rules, read_text
from annolabel.core.workflow import Workflow
from annolabel.modules.batch import batch_document, parse_snapshot, revision, snapshot
from annolabel.modules.discovery import GENERATED_MARKERS, IMAGE_SUFFIXES, discover_images
from annolabel.modules.validation import InputError, validation_details
from annolabel.modules.views import write_preview
from annolabel.schemas.annotations import Document
from annolabel.schemas.task import (
    FolderStartFailure,
    FolderTaskEntry,
    FolderTaskIndex,
    Submission,
    TaskHandle,
)
from annolabel.schemas.workflow import Batch, Rules


class TaskError(InputError):
    """Actionable task failure without implementation details."""


def _context(result: dict, image: str) -> dict:
    """Point a reply at its checkpoint guide and working copy; legacy bundles lack them."""
    task = Path(result["task"])
    guide, submission = task.parent / "guide.txt", task.parent / "submission.json"
    if not submission.is_file():
        return result
    submit = f"annolabel submit {quote(str(task))} --file {quote(str(submission))}"
    export = f"annolabel export {quote(image)} --output NEW_DATASET"
    if result["status"] == "ready":
        next_step = (
            f"Read {quote(str(guide))}, open {quote(result['view'])}, edit {quote(str(submission))} "
            f"as a complete snapshot, then run {submit}."
        )
    elif result["passes_left"]:
        next_step = (
            f"Open {quote(result['view'])} to review. If a correction is needed, edit "
            f"{quote(str(submission))} and run {submit}; otherwise stop and run {export}."
        )
    else:
        next_step = (
            f"Open {quote(result['view'])} to review. No passes are left: stop labeling, report "
            f"any uncertainty, then run {export}."
        )
    return {**result, "guide": str(guide), "submission": str(submission), "next": next_step}


def start_task(
    image: str,
    output: str,
    *,
    instructions: str | None = None,
    categories: str | None = None,
    geometry: str = "both",
    max_passes: int = 2,
    keep_going: bool = False,
) -> dict:
    """Create a coordinate view and immutable initial task handle."""
    if Path(image).expanduser().is_dir():
        return _start_folder(
            image,
            output,
            rules=read_rules(instructions, categories, geometry),
            max_passes=max_passes,
            keep_going=keep_going,
        )
    if keep_going:
        raise InputError(
            "VALIDATION_ERROR",
            f"--keep-going applies to folders; {image!r} is a single image.",
            f"Rerun annolabel task {quote(image)} --output NEW_DIR without --keep-going.",
        )
    workflow = Workflow(image)
    return _publish(
        workflow,
        Path(output).expanduser().absolute(),
        read_rules(instructions, categories, geometry),
        max_passes,
    )


def _publish(workflow: Workflow, output_path: Path, rules: Rules, max_passes: int) -> dict:
    """Publish the view bundle and its task handle together in one new directory."""
    handle = TaskHandle(
        image=str(workflow.path),
        snapshot=str(output_path / "annotations.json"),
        packet=str(output_path / "packet.json"),
        max_passes=max_passes,
    )
    workflow.prepare(
        str(output_path), rules=rules, short=True, extra_files={"task.json": handle.model_dump()}
    )
    return _context(
        {
            "status": "ready",
            "task": str(output_path / "task.json"),
            "view": str(output_path / "view.png"),
            "width": workflow.image.width,
            "height": workflow.image.height,
            "passes_left": max_passes,
        },
        str(workflow.path),
    )


def _start_problem(error: OSError | ValueError, image: str, index: str | None) -> dict:
    """Describe one source that cannot start and how to retry only it."""
    if isinstance(error, InputError):
        problem = {"error": str(error), "code": error.code, "recovery": error.recovery + " Then"}
    else:
        reason = (error.strerror if isinstance(error, OSError) else None) or str(error)
        problem = {
            "error": f"Cannot start a task for source {image!r}: {reason}.",
            "code": "IO_ERROR" if isinstance(error, OSError) else "VALIDATION_ERROR",
            "recovery": f"Fix or replace {image!r} at the same path, then",
        }
    if index is None:
        problem["recovery"] += (
            " retry annolabel task FOLDER --output NEW_DIR. Nothing was created. To label the "
            "other images now, add --keep-going and later run annolabel task-retry on its tasks.json."
        )
    else:
        problem["recovery"] += (
            f" run annolabel task-retry {quote(index)}. Other images can be labeled meanwhile."
        )
    return problem


def _retry_marker(entry: FolderStartFailure) -> Path:
    """Record kept beside the index once task-retry starts publishing this source's slot."""
    slot = Path(entry.task).parent
    return slot.parent / "retries" / f"{slot.name}.json"


def _start_folder(
    source: str, output: str, *, rules: Rules, max_passes: int, keep_going: bool
) -> dict:
    source_path = Path(source).expanduser().resolve(strict=True)
    output_path = Path(output).expanduser().absolute()
    if output_path.exists() or output_path.is_symlink():
        raise InputError(
            "OUTPUT_EXISTS",
            f"Output directory already exists: {str(output_path)!r}.",
            "Pass a new directory with --output; the existing directory was not replaced.",
        )
    images = discover_images(source_path, output_path.resolve())
    if not images:
        raise InputError(
            "NO_IMAGES",
            f"No source images found in input directory {str(source_path)!r}.",
            "Pass a folder containing source images (supported extensions: "
            + ", ".join(sorted(IMAGE_SUFFIXES))
            + "); generated AnnoLabel bundles are excluded. Retry annolabel task FOLDER --output NEW_DIR.",
        )
    index_path = output_path / "tasks.json"
    output_path.mkdir(parents=True)
    try:
        entries, first = [], None
        for number, image in enumerate(images, start=1):
            slot = output_path / f"{number:06d}"
            try:
                workflow = Workflow(str(image))
            except (OSError, ValueError) as error:
                if not keep_going:
                    problem = _start_problem(error, str(image), None)
                    raise InputError(
                        problem["code"], problem["error"], problem["recovery"]
                    ) from None
                entries.append(
                    FolderStartFailure(
                        image=str(image),
                        task=str(slot / "task.json"),
                        **_start_problem(error, str(image), str(index_path)),
                    )
                )
                continue
            result = _publish(workflow, slot, rules, max_passes)
            first = first or result
            entries.append(
                FolderTaskEntry(
                    image=str(image),
                    **{key: result[key] for key in FolderTaskEntry.model_fields if key != "image"},
                )
            )
        index = FolderTaskIndex(
            source=str(source_path), rules=rules, max_passes=max_passes, tasks=entries
        )
        index_path.write_text(index.model_dump_json(indent=2) + "\n")
    except Exception:
        shutil.rmtree(output_path)
        raise
    failed = sum(isinstance(entry, FolderStartFailure) for entry in entries)
    retry = (
        f"{failed} source(s) could not start; see their errors, fix them, then run "
        f"annolabel task-retry {quote(str(index_path))}."
        if failed
        else ""
    )
    return {
        "status": "attention" if failed else "ready",
        "index": str(index_path),
        "source": str(source_path),
        "images": len(entries),
        "tasks": [entry.model_dump() for entry in entries],
        "next_task": first["task"] if first else None,
        "next": " ".join(
            filter(None, [_folder_next(first, str(index_path)) if first else "", retry])
        ),
    }


def retry_tasks(path: str) -> dict:
    """Start only the indexed sources that failed at start, reusing the frozen rules."""
    index = _folder_index(path)
    if index is None:
        raise TaskError(
            "FOLDER_INDEX_REQUIRED",
            f"{path!r} is not a folder tasks.json.",
            "Pass the tasks.json returned by annolabel task FOLDER --keep-going.",
        )
    retried = []
    for entry in index.tasks:
        if not isinstance(entry, FolderStartFailure):
            continue
        slot, marker = Path(entry.task).parent, _retry_marker(entry)
        if marker.exists() or slot.exists() or slot.is_symlink():
            # Started, occupied or missing-after-start slots are never rebuilt; status reports them.
            continue
        try:
            workflow = Workflow(entry.image)
        except (OSError, ValueError):
            continue
        marker.parent.mkdir(exist_ok=True)
        # Written first, so a deleted slot can never be regenerated with a fresh pass budget.
        with marker.open("x") as file:
            json.dump({"image": entry.image, "task": entry.task}, file)
        try:
            _publish(workflow, slot, index.rules, index.max_passes)
        except BaseException:
            # Forget the attempt only if the slot was never published (the rename is atomic).
            if not (slot.exists() or slot.is_symlink()):
                marker.unlink()
            raise
        retried.append(entry.image)
    status = _folder_status(path, index)
    done = (
        f"task-retry started {len(retried)} source(s)."
        if retried
        else "task-retry started nothing; no failed source could start yet."
    )
    return {**status, "retried": retried, "next": f"{done} {status['next']}"}


def _folder_next(current: dict, index: str) -> str:
    """Lead to one pending image's own context, then back to folder progress."""
    if "submission" in current:
        step = current["next"]
    else:
        step = (
            f"Run annolabel task-status {quote(current['task'])} for its next step, then submit it "
            f"with annolabel submit {quote(current['task'])} --file ANNOTATIONS_JSON."
        )
    return f"{step} Then run annolabel task-status {quote(index)} for folder progress."


def _folder_index(path: str) -> FolderTaskIndex | None:
    try:
        data = parse_json(read_text(path, "task handle"), f"task handle {path!r}")
    except InputError:
        # Non-index inputs retain the existing per-image task diagnostics.
        return None
    if isinstance(data, dict) and data.get("kind") == "folder":
        return read_model(
            path,
            FolderTaskIndex,
            "folder task index",
            "Pass the tasks.json returned by annolabel task FOLDER; restore the original "
            "index if it was changed. Submit snapshots using its per-image task.json handles.",
        )
    return None


def task_status(path: str) -> dict:
    """Recover one image checkpoint or the current progress of a folder index."""
    index = _folder_index(path)
    return _folder_status(path, index) if index is not None else Task(path).status()


def submit_task(path: str, payload: str, *, allow_empty: bool = False) -> dict:
    """Submit one image snapshot, explaining how to use a folder index if supplied."""
    index = _folder_index(path)
    if index is not None:
        status = _folder_status(path, index)
        raise TaskError(
            "IMAGE_TASK_REQUIRED",
            f"{path!r} is a folder task index; submit accepts one image task.json at a time.",
            f"Run annolabel task-status {quote(path)} and use a per-image task handle. "
            + status["next"],
        )
    return Task(path).submit(payload, allow_empty=allow_empty)


def preview_task(path: str, payload: str, output: str, *, allow_empty: bool = False) -> dict:
    """Validate and render an unsaved candidate for one image task; never saves or uses a pass."""
    index = _folder_index(path)
    if index is not None:
        status = _folder_status(path, index)
        raise TaskError(
            "IMAGE_TASK_REQUIRED",
            f"{path!r} is a folder task index; preview accepts one image task.json at a time.",
            f"Run annolabel task-status {quote(path)} and preview a per-image task handle. "
            + status["next"],
        )
    return Task(path).preview(payload, output, allow_empty=allow_empty)


# Directories holding any of these are AnnoLabel output; a preview may not be written inside one.
RESERVED_MARKERS = GENERATED_MARKERS | {"task.json", "receipt.json"}


def _preview_output(output: str) -> Path:
    """Refuse outputs at or below generated bundles, so a preview never occupies a pass path."""
    path = Path(output).expanduser().absolute()
    for parent in [path.resolve(), *path.resolve().parents]:
        if any((parent / marker).exists() for marker in RESERVED_MARKERS):
            raise TaskError(
                "OUTPUT_RESERVED",
                f"Preview output {str(path)!r} is inside AnnoLabel output {str(parent)!r}.",
                "Pass a new --output outside task, pass, folder-task, audit and preview "
                "directories. Nothing was written.",
            )
    return path


def _folder_status(path: str, index: FolderTaskIndex) -> dict:
    entries, pending = [], None
    for entry in index.tasks:
        slot = Path(entry.task).parent
        if isinstance(entry, FolderStartFailure) and not (slot.exists() or slot.is_symlink()):
            marker = _retry_marker(entry)
            failure = entry.model_dump(include={"error", "code", "recovery"})
            if marker.exists():
                failure = {
                    "error": f"Retried task {entry.task!r} for {entry.image!r} is missing.",
                    "code": "TASK_MISSING",
                    "recovery": f"Restore {str(slot)!r} with its saved passes; it is not "
                    "regenerated. Only if task-retry was interrupted before publishing it, delete "
                    f"{str(marker)!r} and rerun annolabel task-retry {quote(path)}.",
                }
            entries.append(
                {
                    "image": entry.image,
                    "task": entry.task if marker.exists() else None,
                    "view": None,
                    "state": "error",
                    "objects": None,
                    "passes_left": None,
                    **failure,
                }
            )
            continue
        try:
            task = Task(entry.task)
            if task.handle.image != entry.image:
                raise TaskError(
                    "TASK_CONFLICT",
                    f"Folder entry {entry.image!r} points to a task for {task.handle.image!r}.",
                    f"Restore the matching per-image handle in {path!r} and rerun task-status.",
                )
            current = task.status()
            if not Path(current["view"]).is_file():
                raise TaskError(
                    "IO_ERROR",
                    f"Task view is unavailable: {current['view']!r}.",
                    f"Restore the view {current['view']!r}, then run annolabel task-status {quote(path)}.",
                )
            if pending is None and current["status"] != "saved":
                pending = current
            entries.append(
                {
                    "image": entry.image,
                    "state": "saved" if current["status"] == "saved" else "pending",
                    **{key: current[key] for key in ["task", "view", "objects", "passes_left"]},
                }
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, InputError):
                failure = {"error": str(error), "code": error.code, "recovery": error.recovery}
                if error.details is not None:
                    failure["details"] = error.details
            else:
                failure = {
                    "error": f"Cannot read checkpoint for task {entry.task!r}.",
                    "code": "IO_ERROR" if isinstance(error, OSError) else "VALIDATION_ERROR",
                    "recovery": f"Restore checkpoint files for {entry.task!r}, then run "
                    f"annolabel task-status {quote(path)}; do not reconstruct revisions.",
                }
                if isinstance(error, OSError):
                    failure["error"] += (
                        f" File {error.filename or entry.task!r}: {error.strerror or 'unavailable'}."
                    )
                elif isinstance(error, ValidationError):
                    failure["details"] = validation_details(error)
            entries.append(
                {
                    "image": entry.image,
                    "task": entry.task,
                    "view": getattr(entry, "view", None),
                    "state": "error",
                    "objects": None,
                    "passes_left": None,
                    **failure,
                }
            )
    saved = sum(entry["state"] == "saved" for entry in entries)
    errors = any(entry["state"] == "error" for entry in entries)
    unstarted = sum(entry["state"] == "error" and entry["task"] is None for entry in entries)
    retry = (
        f"{unstarted} source(s) could not start; fix each as its recovery says, then run "
        f"annolabel task-retry {quote(path)}."
        if unstarted
        else ""
    )
    if pending:
        next_step = " ".join(filter(None, [_folder_next(pending, path), retry]))
    elif sum(entry["state"] == "error" for entry in entries) > unstarted:
        next_step = " ".join(
            filter(
                None,
                [
                    f"Resolve the per-image errors, then run annolabel task-status {quote(path)}.",
                    retry,
                ],
            )
        )
    elif retry:
        next_step = retry
    else:
        next_step = f"annolabel export {quote(index.source)} --output NEW_DATASET"
    return {
        "status": "attention" if errors else ("complete" if saved == len(entries) else "ready"),
        "index": str(Path(path).expanduser().absolute()),
        "source": index.source,
        "tasks": entries,
        "totals": {"images": len(entries), "saved": saved, "pending": len(entries) - saved},
        "next_task": pending["task"] if pending else None,
        "next": next_step,
    }


def _removed_warning(changes: dict) -> str:
    removed = json.dumps(changes["removed"], ensure_ascii=False)
    return f"Removed saved objects: {removed}. Omitted objects are deleted."


class Task:
    """Read or submit a task checkpoint; serialize writers for each source image."""

    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser().absolute()
        self.handle = read_model(
            str(self.path),
            TaskHandle,
            "task handle",
            "Pass the task.json returned by annolabel task, submit, or task-status; "
            "do not pass a categories file, snapshot, or packet.",
        )
        self.workflow = Workflow(self.handle.image)
        self.snapshot = read_model(
            self.handle.snapshot,
            Batch,
            "task snapshot",
            "Restore the task snapshot from this checkpoint; do not reconstruct hashes or revisions.",
        )
        self.next_dir = self.path.parent / f"pass{self.handle.pass_count + 1}"

    def _check_current(self, expected_revision: str) -> None:
        if (
            self.snapshot.image_sha256 != self.workflow.document.image.sha256
            or expected_revision != revision(self.workflow.document)
        ):
            raise TaskError(
                "TASK_CONFLICT",
                "Source image or annotations changed since this checkpoint.",
                "Stop and reconcile the changed source; do not overwrite it or reconstruct hashes.",
            )

    def status(self) -> dict:
        """Recover the latest committed receipt without resubmitting geometry."""
        if self.next_dir.exists():
            receipt = json.loads((self.next_dir / "receipt.json").read_text())
            next_task = Task(receipt["result"]["task"])
            return next_task.status()
        self._check_current(self.snapshot.base_revision)
        receipt_path = self.path.parent / "receipt.json"
        if receipt_path.exists():
            return _context(json.loads(receipt_path.read_text())["result"], self.handle.image)
        return _context(
            {
                "status": "ready",
                "task": str(self.path),
                "view": str(self.path.parent / "view.png"),
                "objects": len(self.snapshot.objects),
                "passes_left": self.handle.max_passes - self.handle.pass_count,
            },
            self.handle.image,
        )

    def _parse(self, payload: str) -> Submission:
        try:
            return Submission.model_validate_json(payload)
        except ValidationError:
            return parse_snapshot(
                payload,
                self.workflow.document,
                self.workflow._packet(self.handle.packet),
                short=True,
            )

    def _check_open(self) -> None:
        """Refuse a terminal handle or changed source; both submit and preview need a fresh pass."""
        if self.handle.pass_count >= self.handle.max_passes:
            raise TaskError(
                "PASS_LIMIT",
                "This task has used its allowed annotation passes.",
                "Stop labeling and report any remaining uncertainty. Export is still available.",
            )
        self._check_current(self.snapshot.base_revision)

    def _candidate(
        self, submission: Submission, command: str, allow_empty: bool
    ) -> tuple[Batch, Document, dict]:
        """Validate a complete snapshot against this checkpoint and list its object changes."""
        batch = Batch(
            image_sha256=self.snapshot.image_sha256,
            base_revision=self.snapshot.base_revision,
            **submission.model_dump(),
        )
        packet = self.workflow._packet(self.handle.packet)
        document = batch_document(batch, self.workflow.document, packet)
        previous = {obj.key: obj for obj in self.snapshot.objects}
        current = {obj.key: obj for obj in Batch.model_validate(snapshot(document)).objects}
        changes = {
            "added": [key for key in current if key not in previous],
            "changed": [
                key for key in current if key in previous and current[key] != previous[key]
            ],
            "removed": [key for key in previous if key not in current],
        }
        removed = json.dumps(changes["removed"], ensure_ascii=False)
        if previous and not current and not allow_empty:
            extra = " --output NEW_DIR" if command == "preview" else ""
            raise TaskError(
                "OBJECTS_REMOVED",
                f"This snapshot would remove every saved object: {removed}.",
                f"Keep the objects {removed} in the complete snapshot, or rerun annolabel {command} "
                f"{quote(str(self.path))} --file ANNOTATIONS_JSON{extra} --allow-empty to remove "
                "them intentionally. Nothing was saved and no pass was used.",
            )
        return batch, document, changes

    def submit(self, payload: str, *, allow_empty: bool = False) -> dict:
        """Validate and save all annotations; an identical retry replays its receipt."""
        submission = self._parse(payload)
        digest = hashlib.sha256(submission.model_dump_json().encode()).hexdigest()
        if self.next_dir.exists():
            receipt = json.loads((self.next_dir / "receipt.json").read_text())
            if receipt["submission_sha256"] != digest:
                raise TaskError(
                    "CHECKPOINT_USED",
                    "This checkpoint already saved a different submission.",
                    f"Run annolabel task-status {quote(str(self.path))} and use the returned task for a correction.",
                )
            self._check_current(receipt["saved_revision"])
            return _context(receipt["result"], self.handle.image)
        self._check_open()
        batch, document, changes = self._candidate(submission, "submit", allow_empty)
        next_handle = TaskHandle(
            image=self.handle.image,
            snapshot=str(self.next_dir / "annotations.json"),
            packet=str(self.next_dir / "packet.json"),
            pass_count=self.handle.pass_count + 1,
            max_passes=self.handle.max_passes,
        )
        result = {
            "status": "saved",
            "task": str(self.next_dir / "task.json"),
            "view": str(self.next_dir / "view.png"),
            "objects": len(submission.objects),
            "passes_left": next_handle.max_passes - next_handle.pass_count,
            "changes": changes,
        }
        if changes["removed"]:
            result["warnings"] = [_removed_warning(changes)]
        receipt = {
            "submission_sha256": digest,
            "saved_revision": revision(document),
            "result": result,
        }
        self.workflow.apply(
            batch.model_dump_json(),
            output=str(self.next_dir),
            packet_path=self.handle.packet,
            short=True,
            extra_files={"task.json": next_handle.model_dump(), "receipt.json": receipt},
        )
        return _context(result, self.handle.image)

    def preview(self, payload: str, output: str, *, allow_empty: bool = False) -> dict:
        """Render a candidate snapshot and its changes without saving or using a pass."""
        submission = self._parse(payload)
        if self.next_dir.exists():
            raise TaskError(
                "CHECKPOINT_USED",
                "This checkpoint already saved a submission; previews need the latest task handle.",
                f"Run annolabel task-status {quote(str(self.path))} for the latest task handle and "
                "its passes left; preview that handle if a pass remains.",
            )
        self._check_open()
        destination = _preview_output(output)
        _, document, changes = self._candidate(submission, "preview", allow_empty)
        candidate = {key: snapshot(document)[key] for key in ("classifications", "objects")}
        text = json.dumps(candidate, indent=2) + "\n"
        removed = None
        if changes["removed"]:
            removed = Document(
                image=document.image,
                annotations=[
                    a
                    for a in self.workflow.document.annotations
                    if a.object_id in changes["removed"]
                ],
            )
        working = self.path.parent / "submission.json"
        # Legacy bundles have no working copy; never point at a file that does not exist.
        edit = quote(str(working)) if working.is_file() else "ANNOTATIONS_JSON"
        submit = (
            f"annolabel submit {quote(str(self.path))} --file "
            f"{quote(str(destination / 'candidate.json'))}"
            + (" --allow-empty" if changes["removed"] and not candidate["objects"] else "")
        )
        result = {
            "status": "unsaved",
            "task": str(self.path),
            "view": str(destination / "view.png"),
            "changes_view": str(destination / "changes.png") if removed else None,
            "saved_view": str(self.path.parent / "view.png"),
            "candidate": str(destination / "candidate.json"),
            "candidate_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "objects": len(candidate["objects"]),
            "passes_left": self.handle.max_passes - self.handle.pass_count,
            "changes": changes,
        }
        if changes["removed"]:
            result["warnings"] = [_removed_warning(changes)]
        result["guide"] = str(destination / "guide.txt")
        result["submit"] = submit
        result["next"] = (
            "Nothing was saved and no pass was used. Open "
            + quote(result["view"])
            + (f" and {quote(result['changes_view'])}" if removed else "")
            + f", and compare with the saved view {quote(result['saved_view'])}. To save exactly "
            f"this candidate, run {submit}. To change it, edit {edit} and run "
            f"annolabel preview {quote(str(self.path))} --file {edit} --output "
            "NEW_PREVIEW_DIR."
        )
        guide = (
            "AnnoLabel candidate preview: UNSAVED. No annotations were written and no pass was used.\n"
            "This folder is not a task: it has no task handle and no revision. Submit only with the\n"
            "task handle named below.\n"
            f"\nview.png: the candidate outlines on the {document.image.width} x {document.image.height} "
            "EXIF-oriented source pixels,\nas the saved view would show them after submit.\n"
            + (
                "changes.png: DIAGNOSTIC ONLY. Dashed white outlines labelled REMOVED are saved objects\n"
                "this candidate deletes; they will not be kept. Solid outlines are the candidate.\n"
                if removed
                else "No saved object is removed, so there is no changes.png.\n"
            )
            + "candidate.json: the complete validated snapshot shown here, in original pixels (read-only).\n"
            f"\nSaved state: {result['saved_view']} ({result['passes_left']} pass(es) left).\n"
            f"Changes: {json.dumps(changes, ensure_ascii=False)}\n"
            f"\nTo save this candidate: {submit}\n"
            "Submit validates again against the current source image and labels, and refuses if they\n"
            "changed after this preview.\n"
            f"To change it: edit {edit}, then preview again to a NEW --output directory.\n"
        )
        write_preview(
            self.workflow.image,
            document,
            removed,
            destination,
            {
                "candidate.json": text,
                "preview.json": json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                "guide.txt": guide,
            },
        )
        return result

"""Short annotation tasks with immutable checkpoints and compact receipts."""

import hashlib
import json
import shutil
from pathlib import Path
from shlex import quote

from pydantic import ValidationError

from annolabel.core.inputs import parse_json, read_model, read_text
from annolabel.core.workflow import Workflow
from annolabel.modules.batch import batch_document, parse_snapshot, revision, snapshot
from annolabel.modules.discovery import IMAGE_SUFFIXES, discover_images
from annolabel.modules.validation import InputError, validation_details
from annolabel.schemas.task import FolderTaskEntry, FolderTaskIndex, Submission, TaskHandle
from annolabel.schemas.workflow import Batch


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
) -> dict:
    """Create a coordinate view and immutable initial task handle."""
    if Path(image).expanduser().is_dir():
        return _start_folder(
            image,
            output,
            instructions=instructions,
            categories=categories,
            geometry=geometry,
            max_passes=max_passes,
        )
    workflow = Workflow(image)
    output_path = Path(output).expanduser().absolute()
    handle = TaskHandle(
        image=str(workflow.path),
        snapshot=str(output_path / "annotations.json"),
        packet=str(output_path / "packet.json"),
        max_passes=max_passes,
    )
    workflow.prepare(
        str(output_path),
        instructions=instructions,
        categories=categories,
        geometry=geometry,
        short=True,
    )
    try:
        (output_path / "task.json").write_text(handle.model_dump_json(indent=2) + "\n")
    except Exception:
        shutil.rmtree(output_path)
        raise
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


def _start_folder(
    source: str,
    output: str,
    *,
    instructions: str | None,
    categories: str | None,
    geometry: str,
    max_passes: int,
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
    output_path.mkdir(parents=True)
    try:
        entries, first = [], None
        for number, image in enumerate(images, start=1):
            result = start_task(
                str(image),
                str(output_path / f"{number:06d}"),
                instructions=instructions,
                categories=categories,
                geometry=geometry,
                max_passes=max_passes,
            )
            first = first or result
            entries.append(
                FolderTaskEntry(
                    image=str(image),
                    **{key: result[key] for key in FolderTaskEntry.model_fields if key != "image"},
                )
            )
        index = FolderTaskIndex(source=str(source_path), tasks=entries)
        index_path = output_path / "tasks.json"
        index_path.write_text(index.model_dump_json(indent=2) + "\n")
    except Exception:
        shutil.rmtree(output_path)
        raise
    return {
        "status": "ready",
        "index": str(index_path),
        "source": str(source_path),
        "images": len(entries),
        "tasks": [entry.model_dump() for entry in entries],
        "next_task": entries[0].task,
        "next": _folder_next(first, str(index_path)),
    }


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


def _folder_status(path: str, index: FolderTaskIndex) -> dict:
    entries, pending = [], None
    for entry in index.tasks:
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
                    "view": entry.view,
                    "state": "error",
                    "objects": None,
                    "passes_left": None,
                    **failure,
                }
            )
    saved = sum(entry["state"] == "saved" for entry in entries)
    errors = any(entry["state"] == "error" for entry in entries)
    if pending:
        next_step = _folder_next(pending, path)
    elif errors:
        next_step = f"Resolve the per-image errors, then run annolabel task-status {quote(path)}."
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

    def submit(self, payload: str, *, allow_empty: bool = False) -> dict:
        """Validate and save all annotations; an identical retry replays its receipt."""
        try:
            submission = Submission.model_validate_json(payload)
        except ValidationError:
            submission = parse_snapshot(
                payload,
                self.workflow.document,
                self.workflow._packet(self.handle.packet),
                short=True,
            )
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
        if self.handle.pass_count >= self.handle.max_passes:
            raise TaskError(
                "PASS_LIMIT",
                "This task has used its allowed annotation passes.",
                "Stop labeling and report any remaining uncertainty. Export is still available.",
            )
        self._check_current(self.snapshot.base_revision)
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
            raise TaskError(
                "OBJECTS_REMOVED",
                f"This snapshot would remove every saved object: {removed}.",
                f"Keep the objects {removed} in the complete snapshot, or rerun annolabel submit "
                f"{quote(str(self.path))} --file ANNOTATIONS_JSON --allow-empty to remove them "
                "intentionally. Nothing was saved and no pass was used.",
            )
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
            result["warnings"] = [f"Removed saved objects: {removed}. Omitted objects are deleted."]
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

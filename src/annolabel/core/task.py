"""Short annotation tasks with immutable checkpoints and compact receipts."""

import hashlib
import json
import shutil
from pathlib import Path
from shlex import quote

from pydantic import ValidationError

from annolabel.core.inputs import read_model
from annolabel.core.workflow import Workflow
from annolabel.modules.batch import batch_document, parse_snapshot, revision, snapshot
from annolabel.modules.discovery import IMAGE_SUFFIXES, discover_images
from annolabel.modules.validation import InputError
from annolabel.schemas.task import FolderTaskEntry, FolderTaskIndex, Submission, TaskHandle
from annolabel.schemas.workflow import Batch


class TaskError(InputError):
    """Actionable task failure without implementation details."""


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
    return {
        "status": "ready",
        "task": str(output_path / "task.json"),
        "view": str(output_path / "view.png"),
        "width": workflow.image.width,
        "height": workflow.image.height,
        "passes_left": max_passes,
    }


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
        entries = []
        for number, image in enumerate(images, start=1):
            result = start_task(
                str(image),
                str(output_path / f"{number:06d}"),
                instructions=instructions,
                categories=categories,
                geometry=geometry,
                max_passes=max_passes,
            )
            entries.append(
                FolderTaskEntry(
                    image=str(image),
                    **{key: value for key, value in result.items() if key != "status"},
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
        "next": "Open each view, then run annolabel submit TASK_JSON --file ANNOTATIONS_JSON "
        f"using that image's task handle; start with {quote(entries[0].task)}.",
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
            return json.loads(receipt_path.read_text())["result"]
        return {
            "status": "ready",
            "task": str(self.path),
            "view": str(self.path.parent / "view.png"),
            "objects": len(self.snapshot.objects),
            "passes_left": self.handle.max_passes - self.handle.pass_count,
        }

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
            return receipt["result"]
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
        return result

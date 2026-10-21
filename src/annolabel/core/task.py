"""Short annotation tasks with immutable checkpoints and compact receipts."""

import hashlib
import json
import shutil
from pathlib import Path
from shlex import quote

from pydantic import ValidationError

from annolabel.core.inputs import read_model
from annolabel.core.workflow import Workflow
from annolabel.modules.batch import batch_document, parse_snapshot, revision
from annolabel.modules.validation import InputError
from annolabel.schemas.task import Submission, TaskHandle
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

    def submit(self, payload: str) -> dict:
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
        }
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

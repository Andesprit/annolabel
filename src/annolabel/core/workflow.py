"""Prepare, atomically apply, and review agent annotation snapshots."""

import shutil
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from annolabel.core.annolabel import AnnoLabel
from annolabel.core.inputs import parse_json, read_categories, read_model, read_text
from annolabel.modules.batch import batch_document, check_packet, parse_snapshot, revision
from annolabel.modules.validation import InputError, validation_details
from annolabel.modules.views import write_bundle
from annolabel.schemas.workflow import Packet, Region, Rules


class Workflow(AnnoLabel):
    """Agent workflow facade over the existing annotation sidecars."""

    def _packet(self, path: str | None) -> Packet | None:
        packet = (
            read_model(
                path,
                Packet,
                "--packet",
                "Pass the packet.json returned by prepare, task, or the latest review for this image.",
            )
            if path
            else None
        )
        if packet:
            try:
                check_packet(packet, self.document)
            except ValueError as error:
                raise InputError(
                    "VALIDATION_ERROR",
                    f"Invalid --packet file {path!r}: {error}",
                    "Pass the packet.json returned by prepare, task, or the latest review for this image.",
                ) from None
        return packet

    def prepare(
        self,
        output: str,
        *,
        instructions: str | None = None,
        categories: str | None = None,
        geometry: str = "both",
        short: bool = False,
    ) -> dict:
        """Create original/grid views, current snapshot, schema, and task context."""
        rules = Rules(
            geometry=geometry,
            **({"instructions": read_text(instructions, "--instructions")} if instructions else {}),
            categories=read_categories(categories) if categories else None,
        )
        return write_bundle(
            self.image, self.document, output, rules=rules, prepare=True, short=short
        )

    def review(
        self,
        output: str,
        *,
        packet_path: str | None = None,
        regions: str | None = None,
        per_page: int = 2,
        padding: float = 0.25,
    ) -> dict:
        """Create paired original/annotated views for objects or supplied regions."""
        packet = self._packet(packet_path)
        selected = None
        if regions:
            data = parse_json(read_text(regions, "--regions"), f"--regions file {regions!r}")
            try:
                selected = TypeAdapter(list[Region]).validate_python(data)
            except ValidationError as error:
                raise InputError(
                    "VALIDATION_ERROR",
                    f"Invalid --regions file {regions!r}.",
                    "Supply --regions as a JSON array of {key, box:[x1,y1,x2,y2]} objects in source pixels.",
                    validation_details(error),
                ) from None
        return write_bundle(
            self.image,
            self.document,
            output,
            rules=packet.rules if packet else Rules(),
            regions=selected,
            per_page=per_page,
            padding=padding,
        )

    def apply(
        self,
        payload: str,
        *,
        output: str,
        packet_path: str | None = None,
        short: bool = False,
        extra_files: dict[str, dict] | None = None,
    ) -> dict:
        """Replace a full snapshot and render its review; failures preserve labels."""
        packet = self._packet(packet_path)
        batch = parse_snapshot(payload, self.document, packet)
        document = batch_document(batch, self.document, packet)
        bundle = write_bundle(
            self.image,
            document,
            output,
            rules=packet.rules if packet else Rules(),
            short=short,
            extra_files=extra_files,
        )
        try:
            # Optimistic stale edit detection, not a lock: keep one writer per image.
            if revision(
                AnnoLabel(str(self.path), image_service=self.image_service).document
            ) != revision(self.document):
                raise ValueError("source annotations changed during apply; prepare again")
            self._save(document.annotations)
        except Exception:
            shutil.rmtree(bundle["output"])
            raise
        return {
            **bundle,
            "sidecar": str(self.sidecar),
            "operation": "replace_snapshot",
            "objects": [
                {"key": a.object_id, "annotation_id": a.id, "kind": a.kind}
                for a in self.document.annotations
                if a.object_id is not None
            ],
        }

    def default_review_output(self, file: str) -> str:
        """Place generated reviews beside the batch file, outside its source inputs."""
        parent = Path(file).expanduser().absolute().parent if file != "-" else Path.cwd()
        return str(parent / "annolabel-reviews" / f"{self.path.stem}-{uuid4().hex[:8]}")

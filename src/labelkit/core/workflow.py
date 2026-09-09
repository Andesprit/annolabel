"""Prepare, atomically apply, and review agent annotation snapshots."""
import json
import shutil
from pathlib import Path
from uuid import uuid4
from pydantic import TypeAdapter
from labelkit.core.labelkit import LabelKit
from labelkit.modules.batch import batch_document, check_packet, revision
from labelkit.modules.views import write_bundle
from labelkit.schemas.workflow import Batch, Packet, Region, Rules


class Workflow(LabelKit):
    """Agent workflow facade over the existing annotation sidecars."""

    def _packet(self, path: str | None) -> Packet | None:
        packet = Packet.model_validate_json(Path(path).expanduser().read_text()) if path else None
        if packet:
            check_packet(packet, self.document)
        return packet

    def prepare(self, output: str, *, instructions: str | None = None,
                categories: str | None = None, geometry: str = "both", short: bool = False) -> dict:
        """Create original/grid views, current snapshot, schema, and task context."""
        rules = Rules(geometry=geometry,
                      **({"instructions": Path(instructions).expanduser().read_text()} if instructions else {}),
                      categories=json.loads(Path(categories).expanduser().read_text()) if categories else None)
        return write_bundle(self.image, self.document, output, rules=rules, prepare=True, short=short)

    def review(self, output: str, *, packet_path: str | None = None, regions: str | None = None,
               per_page: int = 2, padding: float = .25) -> dict:
        """Create paired original/annotated views for objects or supplied regions."""
        packet = self._packet(packet_path)
        selected = TypeAdapter(list[Region]).validate_json(Path(regions).expanduser().read_text()) if regions else None
        return write_bundle(self.image, self.document, output, rules=packet.rules if packet else Rules(),
                            regions=selected, per_page=per_page, padding=padding)

    def apply(self, payload: str, *, output: str, packet_path: str | None = None,
              short: bool = False, extra_files: dict[str, dict] | None = None) -> dict:
        """Replace a full snapshot and render its review; failures preserve labels."""
        batch = Batch.model_validate_json(payload)
        packet = self._packet(packet_path)
        document = batch_document(batch, self.document, packet)
        bundle = write_bundle(self.image, document, output, rules=packet.rules if packet else Rules(),
                              short=short, extra_files=extra_files)
        try:
            # Optimistic stale edit detection, not a lock: keep one writer per image.
            if revision(LabelKit(str(self.path)).document) != revision(self.document):
                raise ValueError("source annotations changed during apply; prepare again")
            self._save(document.annotations)
        except Exception:
            shutil.rmtree(bundle["output"])
            raise
        return {**bundle, "sidecar": str(self.sidecar), "operation": "replace_snapshot",
                "objects": [{"key": a.object_id, "annotation_id": a.id, "kind": a.kind}
                            for a in self.document.annotations if a.object_id is not None]}

    def default_review_output(self, file: str) -> str:
        """Place generated reviews beside the batch file, outside its source inputs."""
        parent = Path(file).expanduser().absolute().parent if file != "-" else Path.cwd()
        return str(parent / "labelkit-reviews" / f"{self.path.stem}-{uuid4().hex[:8]}")

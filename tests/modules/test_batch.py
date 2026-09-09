"""Pure snapshot conversion and stable object identities."""

import pytest

from annolabel.modules.batch import batch_document, revision, snapshot
from annolabel.schemas.annotations import Document
from annolabel.schemas.workflow import Batch


def test_snapshot_roundtrip_keeps_identity_and_derives_bounds(document: Document) -> None:
    batch = Batch(
        image_sha256=document.image.sha256,
        base_revision=revision(document),
        classifications=[],
        objects=[{"key": "leaf", "label": "leaf", "polygon": [[10, 10], [40, 10], [25, 50]]}],
    )
    annotated = batch_document(batch, document)
    editable = snapshot(annotated)
    assert editable["objects"][0]["box"] == [10, 10, 40, 50]
    assert batch_document(Batch.model_validate(editable), annotated) == annotated
    with pytest.raises(ValueError, match="stale"):
        batch_document(batch, annotated)

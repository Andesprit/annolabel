"""Export failures through the injected image-service contract."""

from pathlib import Path
from unittest.mock import create_autospec

import pytest
from PIL import Image

from annolabel.modules.coco import export_coco
from annolabel.schemas.annotations import Document
from annolabel.services.images.base import ImageServiceBase


@pytest.mark.parametrize("failure", ["io", "changed"])
def test_source_failure_removes_staged_export(
    source: Path, document: Document, tmp_path: Path, failure: str
) -> None:
    service = create_autospec(ImageServiceBase, instance=True)
    if failure == "io":
        service.load.side_effect = OSError("image read failed")
    else:
        service.load.return_value = (
            Image.new("RGB", (100, 80)),
            document.image.model_copy(update={"sha256": "changed"}),
        )
    output = tmp_path / "dataset"
    with pytest.raises((OSError, ValueError), match="read failed|changed during export"):
        export_coco([(source, document)], str(output), image_service=service)
    service.load.assert_called_once_with(source)
    assert not output.exists()
    assert not list(tmp_path.glob(".annolabel-export-*"))

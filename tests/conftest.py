"""Shared local-image fixtures; the package needs no credentials or env vars."""
from pathlib import Path
import pytest
from PIL import Image
from annolabel.schemas.annotations import Document
from annolabel.services.images.local import LocalImageService


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Create a real image with spaces in its filename.

    :param tmp_path: Isolated test directory.
    :returns: Source image path.
    """
    path = tmp_path / "image with spaces.png"
    Image.new("RGB", (100, 80), "white").save(path)
    return path


@pytest.fixture
def document(source: Path) -> Document:
    """Create an empty annotation document for the source fixture.

    :param source: Local image path.
    :returns: Validated document matching the source bytes.
    """
    _, info = LocalImageService().load(source)
    return Document(image=info)
